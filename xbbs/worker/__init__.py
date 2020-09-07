# SPDX-License-Identifier: AGPL-3.0-only
import attr
import gevent.monkey
gevent.monkey.patch_all()
import gevent
from hashlib import blake2b
import gevent.fileobject as gfobj
from logbook import Logger, StderrHandler, StreamHandler
import signal
import toml
import valideer as V
import os
import os.path as path
import xbbs.util as xutils
import xbbs.messages as msgs
import zmq.green as zmq
import pathlib
import requests
import shutil
from socket import getfqdn
from subprocess import check_call, Popen
import subprocess
import tarfile
import time
from urllib.parse import urlparse
import yaml

with V.parsing(required_properties=True, additional_properties=None):
    CONFIG_VALIDATOR = V.parse({
        "submit_endpoint": "string"
    });


@attr.s
class XbbsWorker:
    current_project = attr.ib(default=None)
    current_job = attr.ib(default=None)
    zmq = attr.ib(default=zmq.Context.instance())


def download(url, to):
    src = urlparse(url, scheme='file')
    if src.scheme == 'file':
        shutil.copy(src.path, to)
    else:
        r = requests.get(url, stream=True)
        # doesnt need to be coop - this is in startup, done in sync
        with open(to, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)

CHUNK_SIZE = 32 * 1024
def upload(inst, locked_sock, job, kind, name, fpath):
    with gfobj.FileObjectThread(fpath, "rb") as toupload:
        data = toupload.read(CHUNK_SIZE)
        last_hash = b"initial"
        while len(data):
            m = msgs.ChunkMessage(last_hash, data).pack()
            last_hash = blake2b(m).digest()
            with locked_sock as sock:
                sock.send_multipart([b"chunk", m])
            data = toupload.read(CHUNK_SIZE)
        with locked_sock as sock:
            sock.send_multipart([b"artifact", msgs.ArtifactMessage(
                    job.project, kind, name, True,
                    path.basename(fpath), last_hash
                ).pack()])


def send_fail(inst, locked_sock, job, kind, name):
    with locked_sock as sock:
        sock.send_multipart([b"artifact", msgs.ArtifactMessage(
                job.project, kind, name, False
            ).pack()])


def parse_yaml_stream(stream):
    buf = ""
    for line in stream:
        buf += line
        if line.strip() == "...":
            yield yaml.safe_load(buf)
            buf = ""
    if len(buf) > 0:
        yield yaml.safe_load(buf)


def process_repo_url(url):
    src = urlparse(url, scheme='file')
    if src.scheme == 'file':
        return src.path
    elif src.scheme in ["http", "https"]:
        return url
    else:
        raise RuntimeError("url must be file or http(s)")


# TODO(arsen): all output needs to be redirected to a pty
def run_job(inst, sock, job, logfd):
    start = time.monotonic()
    code = -1.0
    log.info("running job {}", job)
    build_dir = path.normpath(job.build_root)
    source_dir = f"{build_dir}.src"
    tools_dir = path.join(build_dir, "tools")
    sysroot = path.join(build_dir, "system-root")
    repo_dir = path.join(build_dir, "xbps-repo")
    read_end = None
    siteyaml_file = path.join(build_dir, "bootstrap-site.yml")
    uploads = []
    def runcmd(cmd, **kwargs):
        log.info("running command {} (params {})", cmd, kwargs)
        return check_call(cmd, **kwargs,
                          stdout=logfd, stderr=logfd, stdin=subprocess.DEVNULL)
    def popencmd(cmd, **kwargs):
        log.info("running command {} (params {})", cmd, kwargs)
        return Popen(cmd, **kwargs,
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

        xutils.run_hook(log, source_dir, build_dir, "prejob", outfd=logfd)

        runcmd(["xbstrap", "init", source_dir], cwd=build_dir)
        with open(siteyaml_file, "w") as siteyml:
            siteyml.write('{"pkg_management":{"format":"xbps"}}\n')
        if job.xbps_keys:
            # XXX: this assumes standard xbps paths relative to sysroot
            keysdir = path.join(sysroot, "var/db/xbps/keys")
            os.makedirs(keysdir)
            for fingerprint, pubkey in job.xbps_keys.items():
                keyfile = path.join(keysdir, f"{fingerprint}.plist")
                with open(keyfile, "wb") as pkf:
                    pkf.write(pubkey)
        for x in job.needed_pkgs:
            runcmd(["xbps-install", "-vy",
                    "-R", process_repo_url(job.pkg_repo),
                    "-r", sysroot,
                    "-SM", x])
        for x in job.needed_tools:
            tool_dir = path.join(tools_dir, x)
            os.mkdir(tool_dir)
            tool_tar = path.join(tools_dir, f"{x}.tar.gz")
            download(f"{job.tool_repo}/{x}.tar.gz", tool_tar)
            with tarfile.open(tool_tar, "r") as tar:
                tar.extractall(path=tool_dir)

        (read_end, write_end) = os.pipe()
        with popencmd(["xbstrap-pipeline", "run-job", "--keep-going",
                       "--progress-file", f"fd:{write_end}", job.job],
                      cwd=build_dir, pass_fds=(write_end,)) as runner, \
             xutils.open_coop(read_end, mode="rt", buffering=1) as progress:
            # make sure that the subprocess being done makes this pipe EOF
            os.close(write_end)
            del write_end
            del read_end
            for x in parse_yaml_stream(progress):
                # TODO(arsen): validate
                log.debug("got notify {}", x)
                subject = x["subject"]
                action = x["action"]
                status = x["status"]
                # TODO(arsen): move filename generation to the stream
                if action == "archive-tool":
                    prod_set = job.prod_tools
                    kind = "tool"
                    filename = path.join(tools_dir, f"{subject}.tar.gz")
                elif action == "pack":
                    prod_set = job.prod_pkgs
                    kind = "package"
                    filename = path.join(repo_dir,
                                         f"{subject}-0.0_0.x86_64.xbps")
                else:
                    continue
                if status == "success":
                    repglet = gevent.spawn(upload,
                                           inst, sock, job, kind, subject,
                                           filename)
                else:
                    repglet = gevent.spawn(send_fail,
                                           inst, sock, job, kind, subject)
                uploads.append(repglet)
                repglet.link(lambda g, p=prod_set, s=subject: p.remove(s))
        code = runner.returncode
        log.info("job done. return code: {}", runner.returncode)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        log.exception("job {} failed due to an exception", job)
    finally:
        gevent.joinall(uploads)
        # these do not need to be async since there's no pipe waiting
        # if some artifact wasn't done, that's an error
        for x in job.prod_pkgs:
            send_fail(inst, sock, job, "package", x)
        for x in job.prod_tools:
            send_fail(inst, sock, job, "tool", x)
        with sock as us:
            us.send_multipart([b"job", msgs.JobCompletionMessage(
                project=job.project,
                job=job.job,
                exit_code=code,
                run_time=time.monotonic() - start
            ).pack()])
        try:
            shutil.rmtree(build_dir)
        except FileNotFoundError:
            pass
        try:
            shutil.rmtree(source_dir)
        except FileNotFoundError:
            pass

def collect_logs(job, output, fd):
    with xutils.open_coop(fd, "rt", buffering=1) as pipe:
        for line in pipe:
            with output as sock:
                sock.send_multipart([b"log",
                    msgs.LogMessage(
                        project=job.project,
                        job=job.job,
                        line=line
                    ).pack()
                ])

def main():
    global log
    StderrHandler().push_application()
    log = Logger('xbbs.worker')
    inst = XbbsWorker()

    XBBS_CFG_DIR = os.getenv("XBBS_CFG_DIR", "/etc/xbbs")
    with open(path.join(XBBS_CFG_DIR, "worker.toml"), "r") as fcfg:
        cfg = CONFIG_VALIDATOR.validate(toml.load(fcfg))

    log.info(cfg)
    with inst.zmq.socket(zmq.PULL) as jobs:
        jobs.set_hwm(1)
        jobs.bind(cfg["submit_endpoint"])
        while True:
            log.debug("waiting for job...")
            try:
                job = msgs.JobMessage.unpack(jobs.recv())
                inst.current_project = job.project
                inst.current_job = job.job
                with inst.zmq.socket(zmq.PUSH) as unlocked_out:
                    unlocked_out.set(zmq.LINGER, -1)
                    unlocked_out.connect(job.output)
                    output = xutils.Locked(unlocked_out)
                    (logrd, logwr) = os.pipe()
                    logcoll = gevent.spawn(collect_logs, job, output, logrd)
                    try:
                        with StreamHandler(xutils.open_coop(logwr, mode="w"),
                                           bubble=True):
                            run_job(inst, output, job, logwr)
                    finally:
                        logcoll.join()
            except KeyboardInterrupt:
                log.exception("interrupted")
                break
            except Exception as e:
                log.exception("job error", e)

if __name__ == "__main__":
    main()

# TODO(arsen): make a clean exit
