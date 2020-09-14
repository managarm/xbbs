# SPDX-License-Identifier: AGPL-3.0-only
import attr
import errno
import contextlib
import fcntl
import gevent.fileobject as gfobj
import gevent.lock as glock
import subprocess
import os
import os.path as path
import re


PROJECT_REGEX = re.compile(r"^[a-zA-Z][_a-zA-Z0-9]{0,30}$")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def run_hook(log, source_dir, build_dir, name, *, outfd=None, **extraenv):
    hook_cmd = path.join(source_dir, 'ci/hook')
    if os.access(hook_cmd, os.X_OK):
        e = os.environ.copy()
        e['SOURCE_DIR'] = source_dir
        e['BUILD_DIR'] = build_dir
        e.update(extraenv)
        log.info("executing hook {}", name)
        subprocess.check_call([hook_cmd, name], cwd=build_dir, env=e,
                              stdin=subprocess.DEVNULL,
                              stdout=outfd, stderr=outfd)


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
