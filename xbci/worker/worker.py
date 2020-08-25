#!/usr/bin/python3
# SPDX-License-Identifier: AGPL-3.0-only
import attr
import gevent.monkey
gevent.monkey.patch_all()
import gevent
from hashlib import blake2b
from logbook import Logger, StderrHandler
import signal
import toml
import valideer as V
import os
import os.path as path
import xbci.messages as msgs
import zmq.green as zmq
import pathlib
import shutil
from socket import getfqdn
from subprocess import check_call
import subprocess
import tarfile

with V.parsing(required_properties=True, additional_properties=None):
    CONFIG_VALIDATOR = V.parse({
        "submit_endpoint": "string"
    });

@attr.s
class XbciWorker:
    current_project = attr.ib(default=None)
    current_job = attr.ib(default=None)
    zmq = attr.ib(default=zmq.Context.instance())


def upload(inst, job, kind, name, fpath):
    with inst.zmq.socket(zmq.PUSH) as sock, \
         open(fpath, "rb") as toupload:
        # wait for all messages to be sent before closing
        sock.set(zmq.LINGER, -1)
        sock.connect(job.output)
        data = toupload.read(1024)
        last_hash = b"initial"
        while len(data):
            m = msgs.ChunkMessage(last_hash, data).pack()
            last_hash = blake2b(m).digest()
            sock.send_multipart([b"chunk", m])
            data = toupload.read(1024)
        sock.send_multipart([b"artifact", msgs.ArtifactMessage(
                job.project, kind, name, True,
                path.basename(fpath), last_hash
            ).pack()])


def send_fail(inst, job, kind, name):
    with inst.zmq.socket(zmq.PUSH) as sock:
        # wait for all messages to be sent before closing
        sock.set(zmq.LINGER, -1)
        sock.connect(job.output)
        sock.send_multipart([b"artifact", msgs.ArtifactMessage(
                job.project, kind, name, False
            ).pack()])


# TODO(arsen): all output needs to be redirected to a pty
def run_job(inst, job):
    log.debug("running job {}", job)
    build_dir = path.normpath(job.build_root)
    source_dir = f"{build_dir}.src"
    tool_pack = path.join(build_dir, "tools-pack")
    sysroot = path.join(build_dir, "system-root")
    try:
        os.makedirs(build_dir)
        os.makedirs(source_dir)
        os.mkdir(sysroot)
        os.mkdir(tool_pack)
        # TODO(arsen): put stricter restrictions on build_root
        check_call(["git", "init"], cwd=source_dir)
        check_call(["git", "remote", "add", "origin", job.repository],
                cwd=source_dir)
        check_call(["git", "fetch", "origin"], cwd=source_dir)
        check_call(["git", "checkout", "--detach", job.revision],
                cwd=source_dir)

        check_call(["xbstrap", "init", source_dir], cwd=build_dir)
        with open(path.join(build_dir, "boostrap-site.yml"), "w") as siteyml:
            siteyml.write('{"pkg_management":{"format":"xbps"}}\n')
        for x in job.needed_pkgs:
            check_call(["xbps-install",
                        "-R", job.pkg_repo,
                        "-r", sysroot,
                        "-SM", x])
        # TODO(arsen): extract to xbci.utils.run_hook
        hook_cmd = path.join(source_dir, 'ci/hook')
        if os.access(hook_cmd, os.X_OK):
            e = dict(os.environ.items())
            e['SOURCE_DIR'] = source_dir
            e['BUILD_DIR'] = build_dir
            log.debug("running hook: {}", hook_cmd)
            check_call([hook_cmd, "prejob"], cwd=build_dir, env=e)

        # TODO(arsen): dl deps
        check_call(["xbstrap-pipeline", "run-job", job.job], cwd=build_dir,
                stdin=subprocess.DEVNULL)
        for x in job.prod_pkgs:
            upload(inst, job, "package", x,
                    path.join(build_dir, f"xbps-repo/{x}-0.0_0.x86_64.xbps"))
        for x in job.prod_tools:
            packed = path.join(tool_pack, f"{x}.tar.xz")
            with tarfile.open(packed, "w:xz") as tar:
                tar.add(path.join(build_dir, "tools", x), arcname=".")
            upload(inst, job, "tool", x, packed)
    except Exception as e:
        for x in job.prod_pkgs:
            send_fail(inst, job, "package", x)
        for x in job.prod_tools:
            send_fail(inst, job, "tool", x)
    finally:
        shutil.rmtree(build_dir)
        shutil.rmtree(source_dir)

def main():
    global log
    StderrHandler().push_application()
    log = Logger('xbci.worker')
    inst = XbciWorker()

    XBCI_CFG_DIR = os.getenv("XBCI_CFG_DIR", "/etc/xbci")
    with open(path.join(XBCI_CFG_DIR, "worker.toml"), "r") as fcfg:
        cfg = CONFIG_VALIDATOR.validate(toml.load(fcfg))

    log.info(cfg)
    with inst.zmq.socket(zmq.PULL) as jobs:
        jobs.bind(cfg["submit_endpoint"])
        while True:
            try:
                job = msgs.JobMessage.unpack(jobs.recv())
                inst.current_project = job.project
                inst.current_job = job.job
                run_job(inst, job)
            except Exception as e:
                log.exception("job error", e)
            finally:
                # TODO(arsen): log collection
                inst.current_project = None
                inst.current_job = None

if __name__ == "__main__":
    main()

# TODO(arsen): move pack and dependency download into xbstrap-pipeline, it
# makes more sense than it being here.
# TODO(arsen): notification pipe and remove artifact set transmission
