# SPDX-License-Identifier: AGPL-3.0-only
import argparse
import contextlib
import datetime
import enum
import errno
import fcntl
import hashlib
import ipaddress
import os
import os.path as path
import plistlib
import re
import shutil
import tarfile

import attr
import gevent.fileobject as gfobj
import gevent.lock as glock
import gevent.pool
import valideer as V
import zstandard

PROJECT_REGEX = re.compile(r"^[a-zA-Z][_a-zA-Z0-9]{0,30}$")
DNS_OR_IP_REGEX = re.compile(r"^\[?[:\.\-a-zA-Z0-9]+\]?$")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


# this does not help with normal files
def open_coop(*args, **kwargs):
    # XXX: add fd check?
    f = gfobj.FileObjectPosix(*args, **kwargs)
    fcntl.fcntl(f, fcntl.F_SETFL, os.O_NDELAY)
    return f


class Endpoint(V.String):
    Side = enum.Flag("Side", "BIND CONNECT")

    name = "endpoint"

    def __init__(self, side=Side.BIND | Side.CONNECT, **kwargs):
        super().__init__(**kwargs)
        self.side = side

    def validate(self, value, adapt=True):
        super().validate(value, adapt=adapt)
        transports = {
            "tcp": self._validate_tcp,
            "ipc": self._validate_ipc,
            "inproc": self._validate_inproc,
            "pgm": self._validate_pgm,
            "epgm": self._validate_pgm,
            "vmci": self._validate_vmci,
        }
        transport, sep, endpoint = value.partition("://")
        if not sep:
            raise V.ValidationError("missing transport://")
        if transport not in transports:
            raise V.ValidationError(f"invalid transport {transport!r}")
        transports[transport](endpoint)
        return value

    def _validate_tcp(self, endpoint):
        source_endpoint, sep, endpoint = endpoint.rpartition(";")
        if sep:
            if self.side is not self.Side.CONNECT:
                raise V.ValidationError(
                    "must not specify source in bindable endpoint")
            self._validate_tcp_endpoint(source_endpoint,
                                        wildcard_ok=True)

        self._validate_tcp_endpoint(endpoint,
                                    wildcard_ok=self.side is self.Side.BIND)

    def _validate_tcp_endpoint(self, endpoint, wildcard_ok=False):
        host, port = self._validate_port_pair(endpoint, "host",
                                              wildcard_ok=wildcard_ok)
        if host == "*":
            if not wildcard_ok:
                raise V.ValidationError(
                    "wildcard host not valid in this context")
        elif not DNS_OR_IP_REGEX.match(host):
            raise V.ValidationError(f"host {host!r} does not look like a "
                                    "valid hostname nor an IP address")

    def _validate_ipc(self, endpoint):
        if "\0" in endpoint:
            raise V.ValidationError("paths must not contain NUL bytes")

    def _validate_inproc(self, endpoint):
        if not endpoint:
            raise V.ValidationError("inproc name must not be empty")
        if len(endpoint) > 256:
            raise V.ValidationError("inproc name must not be longer than 256 "
                                    "characters")

    def _validate_pgm(self, endpoint):
        rest, port = self._validate_port_pair(endpoint, "interface;multicast")
        iface, sep, multicast = rest.rpartition(";")
        if not sep:
            raise V.ValidationError("missing semicolon separator")
        # XXX: is it worth validating iface?
        try:
            multicast = ipaddress.IPv4Address(multicast)
        except ipaddress.AddressValueError as e:
            # XXX: what address is causing the error?
            raise V.ValidationError(str(e)) from None
        if not multicast.is_multicast:
            raise V.ValidationError(f"{str(multicast)!r} is not a multicast "
                                    "address")

    def _validate_vmci(self, endpoint):
        iface, port = self._validate_port_pair(
            endpoint, "interface", wildcard_ok=self.side is self.Side.BIND)
        if iface == "*":
            if self.side is not self.Side.BIND:
                raise V.ValidationError(
                    "wildcard interface not valid in this context")
        elif not iface.isdigit():
            raise V.ValidationError(f"interface {iface!r} must be an integer")

    def _validate_port_pair(self, endpoint, rest_name, wildcard_ok=False):
        rest, sep, port = endpoint.rpartition(":")
        if not sep:
            raise V.ValidationError(f"endpoint {endpoint} does not follow the "
                                    f"format of {rest_name}:port")
        if port == "*":
            if not wildcard_ok:
                # XXX: what context?
                raise V.ValidationError(
                    "wildcard port not valid in this context")
        elif not port.isdigit():
            raise V.ValidationError(f"port {port!r} must be an integer")
        return rest, port


@attr.s
class Locked:
    wrapped = attr.ib()
    lock = attr.ib(factory=glock.RLock)

    def __enter__(self):
        self.lock.acquire()
        return self.wrapped

    def __exit__(self, x, y, z):
        self.lock.release()


@contextlib.contextmanager
def lock_file(tdir, category):
    # despite the name, this is not intended to be a lock file, it acts like a
    # lock file, but it would be a pain in the ass to check for it. This
    # function simply exists to mark a directory as currently used
    fname = path.join(tdir, f".{category}.{os.getpid()}.lock")
    fd = os.open(fname, os.O_RDWR | os.O_EXCL | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with os.fdopen(fd, "w+") as f:
            yield f
    finally:
        os.unlink(fname)


def is_locked(tdir, category, pid):
    fname = path.join(tdir, f".{category}.{pid}.lock")
    try:
        with open(fname) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except FileNotFoundError:
        return False
    except OSError as e:
        if e.errno == errno.EWOULDBLOCK:
            return True
        raise


def read_xbps_repodata(ridx):
    with open(ridx, "rb") as zidx:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(zidx) as reader, \
             tarfile.open(fileobj=reader, mode="r|") as t:
            for x in t:
                if x.name != "index.plist":
                    continue
                with t.extractfile(x) as idxpl:
                    pkg_idx = plistlib.load(idxpl, fmt=plistlib.FMT_XML)
                    return pkg_idx


def hash_file(fobj, hashfunc=hashlib.blake2b):
    buf = fobj.read(16 * 1024)
    h = hashfunc()
    while buf:
        h.update(buf)
        buf = fobj.read(16 * 1024)
    return h.digest()


class TristateBooleanAction(argparse.Action):
    def __init__(self,
                 option_strings,
                 dest,
                 **kwargs):
        opts = []
        for opt in option_strings:
            if not opt.startswith("--"):
                raise RuntimeError("tristates can only be flags")
            opts.extend([opt, "--no-" + opt[2:]])

        super(TristateBooleanAction, self).__init__(
            option_strings=opts, dest=dest, **kwargs, nargs=0
        )

    def __call__(self, parser, namespace, values, opt=None):
        if opt in self.option_strings:
            setattr(namespace, self.dest, not opt.startswith("--no-"))

    def format_usage(self):
        return " OR ".join(self.option_strings)


def merge_tree_into(src, dst):
    def _raise(x):
        raise x

    for root, dirs, files in os.walk(src, onerror=_raise):
        for dirn in dirs:
            source = path.join(root, dirn)
            dest = path.join(dst, path.relpath(root, start=src), dirn)
            try:
                os.mkdir(dest)
            except FileExistsError:
                pass
            shutil.copystat(source, dest)
        for filen in files:
            source = path.join(root, filen)
            dest = path.join(dst, path.relpath(root, start=src), filen)
            shutil.copy2(source, dest)


# TODO(arsen): the semantics here are wrong
@V.accepts(x=["string"])
def list_to_set(x):
    return set(x)


def strptime(*args, **kwargs):
    "Parses a timestamp as UTC."
    # unaware datetime objects are dumb
    dt = datetime.datetime.strptime(*args, **kwargs)
    return dt.replace(tzinfo=datetime.timezone.utc)


@contextlib.contextmanager
def autojoin_group(groupclass=gevent.pool.Group, *args, **kwargs):
    # group.close() looks commented out, so I can't prevent further greenlets
    # being spawned. this should be fine in my use case, though
    group = groupclass(*args, **kwargs)
    yield group
    group.join()
