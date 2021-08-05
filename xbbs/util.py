# SPDX-License-Identifier: AGPL-3.0-only
import argparse
import contextlib
import datetime
import errno
import fcntl
import hashlib
import os
import os.path as path
import plistlib
import re
import shutil
import tarfile

import attr
import gevent.fileobject as gfobj
import gevent.lock as glock
import valideer as V
import zstandard

PROJECT_REGEX = re.compile(r"^[a-zA-Z][_a-zA-Z0-9]{0,30}$")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


# this does not help with normal files
def open_coop(*args, **kwargs):
    # XXX: add fd check?
    f = gfobj.FileObjectPosix(*args, **kwargs)
    fcntl.fcntl(f, fcntl.F_SETFL, os.O_NDELAY)
    return f


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
