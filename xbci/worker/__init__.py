# SPDX-License-Identifier: AGPL-3.0-only
import attr
import gevent.monkey
gevent.monkey.patch_all()
import fcntl
import gevent
import gevent.fileobject as gfobj
from hashlib import blake2b
from logbook import Logger, StderrHandler, StreamHandler
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
from subprocess import check_call, Popen
import requests
import subprocess
import tarfile
from urllib.parse import urlparse
import yaml

with V.parsing(required_properties=True, additional_properties=None):
    CONFIG_VALIDATOR = V.parse({
        "submit_endpoint": "string"
    });


# TODO(arsen): extract to utils
def open_coop(*args, **kwargs):
    f = gfobj.FileObjectPosix(*args, **kwargs)
    fcntl.fcntl(f, fcntl.F_SETFL, os.O_NDELAY)
    return f

@attr.s
class XbciWorker:
    current_project = attr.ib(default=None)
    current_job = attr.ib(default=None)
    zmq = attr.ib(default=zmq.Context.instance())


def download(url, to):
    src = urlparse(url, scheme='file')
    if src.scheme == 'file':
        shutil.copy(src.path, to)
    else:
        r = requests.get(url, stream=True)
        with open_coop(to, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024):
                f.write(chunk)

CHUNK_SIZE = 32 * 1024
def upload(inst, sock, job, kind, name, fpath):
    with open_coop(fpath, "rb") as toupload:
        # wait for all messages to be sent before closing
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


def send_fail(inst, sock, job, kind, name):
    # wait for all messages to be sent before closing
    sock.send_multipart([b"artifact", msgs.ArtifactMessage(
            job.project, kind, name, False
        ).pack()])


def parse_yaml_stream(stream):
    buf = ""
    for line in stream:
        buf += line
        if line.strip() == "...":
            # TODO(arsen): safe_load
            yield yaml.safe_load(buf)
            buf = ""
    if len(buf) > 0:
        yield yaml.safe_load(buf)


# TODO(arsen): all output needs to be redirected to a pty
def run_job(inst, sock, job, logfd):
    log.debug("running job {}", job)
    build_dir = path.normpath(job.build_root)
    source_dir = f"{build_dir}.src"
    tools_dir = path.join(build_dir, "tools")
    sysroot = path.join(build_dir, "system-root")
    repo_dir = path.join(build_dir, "xbps-repo")
    notify_pipe = None
    uploads = []
    def runcmd(cmd, **kwargs):
        log.debug("running command {} (params {})", cmd, kwargs)
        return check_call(cmd, **kwargs,
                          stdout=logfd, stderr=logfd, stdin=subprocess.DEVNULL)
    try:
        os.makedirs(build_dir)
        os.makedirs(source_dir)
        os.mkdir(sysroot)
        os.mkdir(tools_dir)
        # TODO(arsen): put stricter restrictions on build_root
        runcmd(["git", "init"], cwd=source_dir)
        runcmd(["git", "remote", "add", "origin", job.repository],
               cwd=source_dir)
        runcmd(["git", "fetch", "origin"], cwd=source_dir)
        runcmd(["git", "checkout", "--detach", job.revision],
               cwd=source_dir)

        # TODO(arsen): extract to xbci.utils.run_hook
        hook_cmd = path.join(source_dir, 'ci/hook')
        if os.access(hook_cmd, os.X_OK):
            e = dict(os.environ.items())
            e['SOURCE_DIR'] = source_dir
            e['BUILD_DIR'] = build_dir
            log.debug("running hook: {}", hook_cmd)
            check_call([hook_cmd, "prejob"], cwd=build_dir, env=e)

        runcmd(["xbstrap", "init", source_dir], cwd=build_dir)
        with open_coop(path.join(build_dir, "bootstrap-site.yml"), "w") as siteyml:
            siteyml.write('{"pkg_management":{"format":"xbps"}}\n')
        for x in job.needed_pkgs:
            check_call(["xbps-install", "-vy",
                        "-R", job.pkg_repo,
                        "-r", sysroot,
                        "-SM", x])
        for x in job.needed_tools:
            tool_dir = path.join(tools_dir, x)
            os.mkdir(tool_dir)
            tool_tar = path.join(tools_dir, f"{x}.tar.gz")
            download(f"{job.tool_repo}/{x}.tar.gz", tool_tar)
            with tarfile.open(tool_tar, "r") as tar:
                tar.extractall(path=tool_dir)

        notify_pipe = os.pipe()
        (read_end, write_end) = notify_pipe
        # TODO(arsen): --keep-going
        with Popen(["xbstrap-pipeline", "run-job",
                    "--progress-file", f"fd:{write_end}", job.job],
                   cwd=build_dir, stdin=subprocess.DEVNULL,
                   stdout=logfd, stderr=logfd,
                   pass_fds=(write_end,)) as runner, \
             open_coop(read_end, mode="rt", buffering=1) as progress:
            # make sure that the subprocess being done makes this pipe EOF
            os.close(write_end)
            for x in parse_yaml_stream(progress):
                # TODO(arsen): validate
                log.debug("got notify {}", x)
                subject = x["subject"]
                action = x["action"]
                status = x["status"]
                # TODO(arsen): move filename generation to the stream
                if action == "archive-tool":
                    job.prod_tools.remove(subject)
                    kind = "tool"
                    filename = path.join(tools_dir, f"{subject}.tar.gz")
                elif action == "pack":
                    job.prod_pkgs.remove(subject)
                    kind = "package"
                    filename = path.join(repo_dir,
                                         f"{subject}-0.0_0.x86_64.xbps")
                else:
                    continue
                # path.join(tools, f"{subject}.tar.gz")
                if status == "success": 
                    uploads.append(gevent.spawn(upload,
                                inst, sock, job, kind, subject, filename))
                else:
                    uploads.append(gevent.spawn(send_fail,
                                inst, sock, job, kind, subject))
        # TODO(arsen): add some sort of way to use that exit code
        # TODO(arsen): remove both of these
    except Exception as e:
        log.exception(f"job {job} failed due to an exception", e)
    finally:
        gevent.joinall(uploads)
        # these do not need to be async since there's no pipe waiting
        for x in job.prod_pkgs:
            send_fail(inst, sock, job, "package", x)
        for x in job.prod_tools:
            send_fail(inst, sock, job, "tool", x)
        shutil.rmtree(build_dir)
        shutil.rmtree(source_dir)
        if notify_pipe:
            for x in notify_pipe:
                try:
                    os.close(x)
                except Exception:
                    pass


def collect_logs(job, output, fd):
    with open_coop(fd, "rt", buffering=1) as pipe:
        for line in pipe:
            output.send_multipart([b"log",
                msgs.LogMessage(
                    project=job.project,
                    job=job.job,
                    line=line
                ).pack()
            ])

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
                with inst.zmq.socket(zmq.PUSH) as output:
                    output.set(zmq.LINGER, -1)
                    output.connect(job.output)
                    (logrd, logwr) = os.pipe()
                    logcoll = gevent.spawn(collect_logs, job, output, logrd)
                    try:
                        with StreamHandler(open_coop(logwr, mode="w")):
                            run_job(inst, output, job, logwr)
                    finally:
                        logcoll.join()
            except Exception as e:
                log.exception("job error", e)

if __name__ == "__main__":
    main()

# TODO(arsen): notification pipe and remove artifact set transmission