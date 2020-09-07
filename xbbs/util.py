# SPDX-License-Identifier: AGPL-3.0-only
import attr
import fcntl
import gevent.fileobject as gfobj
import gevent.lock as glock
import subprocess
import os
import os.path as path
import re


PROJECT_REGEX = re.compile("^[a-zA-Z][_a-zA-Z0-9]{0,30}$")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def run_hook(log, source_dir, build_dir, name, *, outfd=None):
    hook_cmd = path.join(source_dir, 'ci/hook')
    if os.access(hook_cmd, os.X_OK):
        e = dict(os.environ.items())
        e['SOURCE_DIR'] = source_dir
        e['BUILD_DIR'] = build_dir
        log.info("executing hook {}", name)
        subprocess.check_call([hook_cmd, "prejob"], cwd=build_dir, env=e,
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
