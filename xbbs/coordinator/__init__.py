# SPDX-License-Identifier: AGPL-3.0-only
import gevent.monkey; gevent.monkey.patch_all()  # noqa isort:skip
import contextlib
import datetime
import json
import operator
import os
import os.path as path
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from enum import Enum
from hashlib import blake2b

import attr
import gevent
import gevent.event
import msgpack as msgpk
import toml
import valideer as V
import zmq.green as zmq
from logbook import Logger, StderrHandler

import xbbs.messages as msgs
import xbbs.protocol
import xbbs.util as xutils


def check_call_logged(cmd, **kwargs):
    log.info("running command {} (params {})", cmd, kwargs)
    return subprocess.check_call(cmd, **kwargs)


def check_output_logged(cmd, **kwargs):
    log.info("running command {} (params {})", cmd, kwargs)
    return subprocess.check_output(cmd, **kwargs)


# more properties are required than not
with V.parsing(required_properties=True,
               additional_properties=V.Object.REMOVE):
    @V.accepts(x=V.AnyOf("string", {"bind": "string", "connect": "string"}))
    def _receive_adaptor(x):
        if isinstance(x, str):
            return {"bind": x, "connect": x}
        return x

    @V.accepts(x="string")
    def _path_exists(x):
        return os.access(x, os.R_OK)

    # TODO(arsen): write an endpoint validator for workers
    CONFIG_VALIDATOR = V.parse({
        "command_endpoint": V.AdaptBy(_receive_adaptor),
        "project_base": "string",
        "build_root": V.AllOf("string", path.isabs),
        "intake": V.AdaptBy(_receive_adaptor),
        "workers": ["string"],
        # use something like a C identifier, except disallow underscore as a
        # first character too. this is so that we have a namespace for xbbs
        # internal directories, such as collection directories
        "projects": V.Mapping(xutils.PROJECT_REGEX, {
            "git": "string",
            "?description": "string",
            "?classes": V.Nullable(["string"], []),
            "packages": "string",
            "?fingerprint": "string",
            "tools": "string"
        })
    })
    PUBKEY_VALIDATOR = V.parse({
        # I'm only validating the keys that xbbs uses
        "signature-by": "string"
    })

with V.parsing(required_properties=True, additional_properties=None):
    # { job_name: job }
    ARTIFACT_VALIDATOR = V.parse({"name": "string", "version": "string"})
    JOB_REGEX = re.compile(r"^[a-z]+:[a-zA-Z][a-zA-Z-_0-9.]*$")
    GRAPH_VALIDATOR = V.parse(V.Mapping(JOB_REGEX, {
        "products": {
            "tools": [ARTIFACT_VALIDATOR],
            "pkgs": [ARTIFACT_VALIDATOR],
            "files": [V.AdaptBy(operator.itemgetter("name"), {
                "name": "string",
                "filepath": "string"
            })]
        },
        "needed": {
            "tools": [ARTIFACT_VALIDATOR],
            "pkgs": [ARTIFACT_VALIDATOR]
        }
    }))


@attr.s
class Project:
    name = attr.ib()
    git = attr.ib()
    description = attr.ib()
    classes = attr.ib()
    packages = attr.ib()
    tools = attr.ib()
    fingerprint = attr.ib(default=None)
    current = attr.ib(default=None)

    # TODO(arsen): these should probably be moved to RunningProject
    def base(self, xbbs):
        return path.join(xbbs.project_base, self.name)

    def log(self, inst, job=None):
        tsdir = path.join(self.base(inst),
                          self.current.ts.strftime(xutils.TIMESTAMP_FORMAT))
        os.makedirs(tsdir, exist_ok=True)
        if not job:
            return tsdir
        return path.join(tsdir, f"{job}.log")

    def info(self, inst, job):
        tsdir = path.join(self.base(inst),
                          self.current.ts.strftime(xutils.TIMESTAMP_FORMAT))
        os.makedirs(tsdir, exist_ok=True)
        return path.join(tsdir, f"{job}.info")


@attr.s
class Xbbs:
    project_base = attr.ib()
    collection_dir = attr.ib()
    tmp_dir = attr.ib()
    build_root = attr.ib()
    intake_address = attr.ib()
    pipeline = attr.ib(default=None)
    intake = attr.ib(default=None)
    projects = attr.ib(factory=dict)
    project_greenlets = attr.ib(factory=list)
    zmq = attr.ib(default=zmq.Context.instance())

    @classmethod
    def create(cls, cfg):
        pbase = cfg["project_base"]
        inst = Xbbs(
            project_base=pbase,
            collection_dir=path.join(pbase, "_coldir"),
            tmp_dir=path.join(pbase, "_tmp"),
            build_root=cfg["build_root"],
            intake_address=cfg["intake"]["connect"]
        )
        os.makedirs(inst.collection_dir, exist_ok=True)
        os.makedirs(inst.tmp_dir, exist_ok=True)
        return inst


@attr.s
class Artifact:
    Kind = Enum("Kind", "TOOL PACKAGE FILE")
    kind = attr.ib()
    name = attr.ib()
    version = attr.ib()
    received = attr.ib(default=False, eq=False, order=False)
    failed = attr.ib(default=False, eq=False, order=False)


@attr.s
class Job:
    # TODO(arsen): RUNNING is actually waiting to finish: it might say it's
    # running, but it's proobably not, and is instead stuck in the pipeline
    deps = attr.ib(factory=list)
    products = attr.ib(factory=list)
    status = attr.ib(default=msgs.JobStatus.WAITING)
    exit_code = attr.ib(default=None)
    run_time = attr.ib(default=None)

    def fail(self, graph):
        if self.status is msgs.JobStatus.RUNNING:
            self.status = msgs.JobStatus.WAITING_FOR_DONE
        else:
            self.status = msgs.JobStatus.FAILED

        for prod in self.products:
            if prod.failed:
                continue
            prod.failed = True
            prod.received = True
            for job in graph.values():
                if prod in job.deps:
                    job.fail(graph)


@attr.s
class RunningProject:
    name = attr.ib()
    repository = attr.ib()
    revision = attr.ib()
    jobs = attr.ib(factory=dict)
    success = attr.ib(default=True)
    ts = attr.ib(factory=datetime.datetime.now)

    tool_set = attr.ib(factory=dict)
    file_set = attr.ib(factory=dict)
    pkg_set = attr.ib(factory=dict)

    artifact_received = attr.ib(factory=gevent.event.Event)

    @classmethod
    def parse_graph(cls, project, revision, graph):
        graph = GRAPH_VALIDATOR.validate(graph)

        proj = cls(project.name, project.git, revision)
        tools = proj.tool_set
        pkgs = proj.pkg_set
        files = proj.file_set
        for job, info in graph.items():
            # TODO(arsen): circ dep detection (low prio: handled in xbstrap)
            job_val = Job()
            for x in info["needed"]["tools"]:
                name = x["name"]
                if name not in tools:
                    tools[name] = Artifact(Artifact.Kind.TOOL, **x)
                job_val.deps.append(tools[name])
            for x in info["needed"]["pkgs"]:
                name = x["name"]
                if name not in pkgs:
                    pkgs[name] = Artifact(Artifact.Kind.PACKAGE, **x)
                job_val.deps.append(pkgs[name])

            for x in info["products"]["tools"]:
                name = x["name"]
                if name not in tools:
                    tools[name] = Artifact(Artifact.Kind.TOOL, **x)
                job_val.products.append(tools[name])
            for x in info["products"]["pkgs"]:
                name = x["name"]
                if name not in pkgs:
                    pkgs[name] = Artifact(Artifact.Kind.PACKAGE, **x)
                job_val.products.append(pkgs[name])
            for fname in info["products"]["files"]:
                artifact = Artifact(Artifact.Kind.FILE,
                                    name=fname, version=None)
                job_val.products.append(artifact)
                files[fname] = artifact

            proj.jobs[job] = job_val

        return proj


class ArtifactEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Artifact.Kind):
            return obj.name
        return super().default(obj)


def store_jobs(inst, projinfo, *, success=None, length=None):
    coordfile = path.join(projinfo.log(inst), "coordinator")
    job_info = {}
    for name, job in projinfo.current.jobs.items():
        current = {
            "status": job.status.name,
            "deps": [attr.asdict(x) for x in job.deps],
            "products": [attr.asdict(x) for x in job.products]
        }
        job_info[name] = current
        if job.exit_code is not None:
            current.update(exit_code=job.exit_code)
        if job.run_time is not None:
            current.update(run_time=job.run_time)
    # TODO(arsen): store more useful graph
    state = {
        "jobs": job_info
    }
    if success is not None:
        state.update(success=success, run_time=length)
    with open(coordfile, "w") as csf:
        json.dump(state, csf, indent=4, cls=ArtifactEncoder)


def solve_project(inst, projinfo):
    project = projinfo.current
    while True:
        project.artifact_received.clear()
        some_waiting = False
        for name, job in project.jobs.items():
            if all([x.received for x in job.products]) and \
               job.status is msgs.JobStatus.RUNNING:
                job.status = msgs.JobStatus.WAITING_FOR_DONE

            if not job.status.terminating:
                some_waiting = True

            if job.status is not msgs.JobStatus.WAITING:
                continue

            failed = False
            satisfied = True
            for dep in job.deps:
                if not dep.received:
                    satisfied = False
                if dep.failed:
                    failed = True

            if failed:
                job.fail(project.jobs)
                # This failure means that our artifacts might have changed -
                # trigger a rescan
                project.artifact_received.set()
                continue

            if not satisfied:
                continue

            needed_tools = {x.name: x.version for x in job.deps
                            if x.kind is Artifact.Kind.TOOL}
            needed_pkgs = {x.name: x.version for x in job.deps
                           if x.kind is Artifact.Kind.PACKAGE}
            prod_tools = {x.name: x.version for x in job.products
                          if x.kind is Artifact.Kind.TOOL}
            prod_pkgs = {x.name: x.version for x in job.products
                         if x.kind is Artifact.Kind.PACKAGE}
            prod_files = [x.name for x in job.products
                          if x.kind is Artifact.Kind.FILE]
            keys = {}
            if projinfo.fingerprint:
                pubkey = path.join(projinfo.base(inst),
                                   f"{projinfo.fingerprint}.plist")
                # XXX: this is not cooperative, and should be okay because
                # it's a small amount of data
                with open(pubkey, "rb") as pkf:
                    keys = {projinfo.fingerprint: pkf.read()}

            job.status = msgs.JobStatus.RUNNING
            jobreq = msgs.JobMessage(
                project=project.name,
                job=name,
                repository=project.repository,
                revision=project.revision,
                output=inst.intake_address,
                build_root=inst.build_root,
                needed_tools=needed_tools,
                needed_pkgs=needed_pkgs,
                prod_pkgs=prod_pkgs,
                prod_tools=prod_tools,
                prod_files=prod_files,
                tool_repo=projinfo.tools,
                pkg_repo=projinfo.packages,
                xbps_keys=keys
            )
            log.debug("sending job request {}", jobreq)
            inst.pipeline.send(jobreq.pack())

        store_jobs(inst, projinfo)

        # TODO(arsen): handle the edge case in which workers are dead
        if not some_waiting:
            assert all(x.received for x in project.tool_set.values())
            assert all(x.received for x in project.file_set.values())
            assert all(x.received for x in project.pkg_set.values())
            assert all(x.status.terminating for x in project.jobs.values())
            return all(not x.failed for x in project.tool_set.values()) and \
                all(not x.failed for x in project.file_set.values()) and \
                all(not x.failed for x in project.pkg_set.values()) and \
                all(x.status.successful for x in project.jobs.values())

        project.artifact_received.wait()

# TODO(arsen): a better log collection system. It should include the output of
# git and xbstrap, too
# this should be a pty assigned to each build, on the other side of which stuff
# is read out and parsed in a manner similar to a terminal.
# the ideal would be properly rendering control sequences same way
# xterm-{256,}color does, since it is widely adopted and assumed.


def run_project(inst, project):
    start = None
    try:
        projdir = path.join(project.base(inst), 'repo')
        os.makedirs(projdir, exist_ok=True)
        if not path.isdir(path.join(projdir, ".git")):
            check_call_logged(["git", "init"], cwd=projdir)
            check_call_logged(["git", "remote", "add", "origin", project.git],
                              cwd=projdir)
        check_call_logged(["git", "fetch", "origin"], cwd=projdir)
        # TODO(arsen): support non-master builds
        check_call_logged(["git", "checkout", "--detach", "origin/master"],
                          cwd=projdir)
        rev = check_output_logged(["git", "rev-parse", "HEAD"],
                                  cwd=projdir).decode().strip()
        with tempfile.TemporaryDirectory(dir=inst.tmp_dir) as td:
            xutils.run_hook(log, projdir, td, "pregraph")
            check_call_logged(["xbstrap", "init", projdir], cwd=td)
            graph = json.loads(check_output_logged(["xbstrap-pipeline",
                                                    "compute-graph",
                                                    "--artifacts", "--json"],
                                                   cwd=td).decode())
        project.current = RunningProject.parse_graph(project, rev, graph)

        @contextlib.contextmanager
        def _current_symlink():
            # XXX: if this fails two coordinators are running, perhaps that
            # should be prevented somehow (lock on start)?
            current_file = path.join(project.base(inst), "current")
            datedir = project.current.ts.strftime(xutils.TIMESTAMP_FORMAT)
            try:
                yield os.symlink(datedir, current_file)
            finally:
                os.unlink(current_file)

        # XXX: keep last successful and currently running directory as links?
        with xutils.lock_file(project.log(inst), "coordinator"), \
             _current_symlink():
            start = time.monotonic()
            success = solve_project(inst, project)
            length = time.monotonic() - start
            log.info("job {} done; success? {} in {}s",
                     project.name, success, length)
            store_jobs(inst, project, success=success, length=length)
    except Exception:
        log.exception("build failed due to an exception")
        length = 0
        if start is not None:
            length = time.monotonic() - start
        if isinstance(project.current, RunningProject):
            store_jobs(inst, project, success=False, length=length)
    finally:
        project.current = None


def cmd_build(inst, name):
    "handle starting a new build on a project by name"
    name = msgpk.loads(name)
    if name not in inst.projects:
        return 404, msgpk.dumps("unknown project")
    proj = inst.projects[name]
    if proj.current:
        return 409, msgpk.dumps("project already running")
    # XXX: is this a horrible hack? naaaaa
    proj.current = True
    pg = gevent.spawn(run_project, inst, proj)
    pg.link(lambda g, i=inst: inst.project_greenlets.remove(g))
    inst.project_greenlets.append(pg)


def cmd_fail(inst, name):
    "fail any unstarted packages"
    name = msgpk.loads(name)
    if name not in inst.projects:
        return 404, msgpk.dumps("unknown project")
    proj = inst.projects[name]
    if not proj.current:
        return 409, msgpk.dumps("project not running")
    for x in proj.current.jobs.values():
        if x.status is not msgs.JobStatus.WAITING:
            continue
        x.fail(proj.current.jobs)


def cmd_status(inst, _):
    projmap = {}
    for x in inst.projects.values():
        projmap[x.name] = {
            "git": x.git,
            "description": x.description,
            "classes": x.classes,
            "running": bool(x.current)
        }
    return msgs.StatusMessage(
        projects=projmap,
        hostname=socket.gethostname(),
        load=os.getloadavg(),
        pid=os.getpid()
    ).pack()


def command_loop(inst, sock_cmd):
    while True:
        try:
            [command, arg] = sock_cmd.recv_multipart()
            command = command.decode("us-ascii")
            if command not in command_loop.cmds:
                sock_cmd.send_multipart([b"400",
                                         msgpk.dumps("no such command")])
                continue

            code = "200"
            value = command_loop.cmds[command](inst, arg)
            if value is None:
                sock_cmd.send_multipart([b"204", msgpk.dumps("")])
                continue

            if isinstance(value, tuple):
                (code, value) = value
                assert isinstance(code, int)
                code = str(code)

            sock_cmd.send_multipart([code.encode(), value])
        except zmq.ZMQError:
            log.exception("command loop i/o error, aborting")
            return
        except xbbs.protocol.ProtocolError as e:
            log.exception("comand processing error", e)
            sock_cmd.send_multipart([str(e.code).encode(),
                                     msgpk.dumps(f"{type(e).__name__}: {e}")])
        except V.ValidationError as e:
            log.exception("command processing error", e)
            sock_cmd.send_multipart([b"400",
                                     msgpk.dumps(f"{type(e).__name__}: {e}")])
        except Exception as e:
            log.exception("comand processing error", e)
            sock_cmd.send_multipart([b"500",
                                     msgpk.dumps(f"{type(e).__name__}: {e}")])


command_loop.cmds = {
    "build": cmd_build,
    "fail": cmd_fail,
    "status": cmd_status
}


def cmd_chunk(inst, value):
    chunk = msgs.ChunkMessage.unpack(value)
    if chunk.last_hash == b"initial":
        # (fd, path)
        store = tempfile.mkstemp(dir=inst.collection_dir)
        os.fchmod(store[0], 0o644)
    elif chunk.last_hash not in cmd_chunk.table:
        return
    else:
        store = cmd_chunk.table[chunk.last_hash]
        del cmd_chunk.table[chunk.last_hash]
    digest = blake2b(value).digest()
    cmd_chunk.table[digest] = store
    os.write(store[0], chunk.data)


cmd_chunk.table = {}


def maybe_sign_artifact(inst, artifact, project):
    if not project.fingerprint:
        return
    base = project.base(inst)
    privkey = path.join(base, f"{project.fingerprint}.rsa")
    pubkey = path.join(base, f"{project.fingerprint}.plist")
    # XXX: this is not cooperative, and should be okay because
    # it's a small amount of data
    with open(pubkey, "rb") as pkf:
        pkeydata = PUBKEY_VALIDATOR.validate(plistlib.load(pkf))
    signed_by = pkeydata["signature-by"]
    check_call_logged(["xbps-rindex",
                       "--signedby", signed_by,
                       "--privkey", privkey,
                       "-s", path.dirname(artifact)])
    check_call_logged(["xbps-rindex",
                       "--signedby", signed_by,
                       "--privkey", privkey,
                       "-S", artifact])
    # XXX: a sanity check here? extract the key from repodata and compare with
    # "{project.fingerprint}.plist"s key and signer


def cmd_artifact(inst, value):
    "handle receiving an artifact"
    message = msgs.ArtifactMessage.unpack(value)
    log.debug("received artifact {}", message)
    artifact = None
    target = None
    try:
        if message.project not in inst.projects:
            return
        proj = inst.projects[message.project]
        run = inst.projects[message.project].current
        if not run:
            return

        if message.artifact_type == "tool":
            aset = run.tool_set
        elif message.artifact_type == "file":
            aset = run.file_set
        else:
            assert message.artifact_type == "package"
            aset = run.pkg_set

        repo = path.abspath(path.join(proj.log(inst),
                            f"{message.artifact_type}_repo"))
        repo_roll = path.abspath(path.join(proj.base(inst), "rolling",
                                 f"{message.artifact_type}_repo"))
        os.makedirs(repo, exist_ok=True)
        os.makedirs(repo_roll, exist_ok=True)

        if message.artifact not in aset:
            return

        artifact = aset[message.artifact]
        artifact.received = True
        artifact.failed = not message.success
        if not message.success:
            run.artifact_received.set()
            return

        (fd, target) = cmd_chunk.table[message.last_hash]
        del cmd_chunk.table[message.last_hash]
        os.close(fd)

        try:
            artifact_file = path.join(repo, message.filename)
            artifact_roll = path.join(repo_roll, message.filename)
            shutil.move(target, artifact_file)
            if artifact.kind == Artifact.Kind.PACKAGE:
                check_call_logged(["xbps-rindex", "-fa", artifact_file])
                if not path.exists(artifact_roll):
                    shutil.copy2(artifact_file, artifact_roll)
                    # we don't -f this one, because we want the most up-to-date
                    # here
                    check_call_logged(["xbps-rindex", "-a", artifact_roll])
                    # clean up rolling repo
                    check_call_logged(["xbps-rindex", "-r", repo_roll])
                else:
                    with open(artifact_file, "rb") as f:
                        h1 = xutils.hash_file(f)
                    with open(artifact_roll, "rb") as f:
                        h2 = xutils.hash_file(f)
                    if h1 != h2:
                        log.error("{} hash changed, but pkgver didn't!",
                                  artifact)
                maybe_sign_artifact(inst, artifact_file, proj)
                maybe_sign_artifact(inst, artifact_roll, proj)
            # TODO: rolling repo for tools
        except Exception as e:
            log.exception("artifact deposit failed", e)
            artifact.failed = True

        run.artifact_received.set()
    except Exception:
        if artifact:
            artifact.failed = True
        raise
    finally:
        try:
            if target:
                os.unlink(target)
        except FileNotFoundError:
            pass


def cmd_log(inst, value):
    message = msgs.LogMessage.unpack(value)
    if message.project not in inst.projects:
        return
    proj = inst.projects[message.project]
    if not proj.current:
        log.info("dropped log because project {} was not running",
                 message.project)
        return

    # XXX: this is not cooperative, and should be okay because
    # it's a small amount of data
    with open(proj.log(inst, message.job), mode="a") as logfile:
        logfile.write(message.line)


def cmd_job(inst, value):
    message = msgs.JobCompletionMessage.unpack(value)
    log.debug("got job message {}", message)
    if message.project not in inst.projects:
        return
    proj = inst.projects[message.project]
    if not proj.current:
        return

    job = proj.current.jobs[message.job]
    if message.exit_code == 0:
        job.status = msgs.JobStatus.SUCCESS
    else:
        job.status = msgs.JobStatus.FAILED
    job.exit_code = message.exit_code
    job.run_time = message.run_time
    proj.current.artifact_received.set()
    with open(proj.info(inst, message.job), "w") as infofile:
        json.dump(message._dictvalue, infofile, indent=4)


def intake_loop(inst):
    while True:
        try:
            [cmd, value] = inst.intake.recv_multipart()
            cmd = cmd.decode("us-ascii")
            intake_loop.cmds[cmd](inst, value)
        except zmq.ZMQError:
            log.exception("intake pipe i/o error, aborting")
            return
        except Exception as e:
            log.exception("intake pipe error, continuing", e)


intake_loop.cmds = {
    "chunk": cmd_chunk,
    "artifact": cmd_artifact,
    "job": cmd_job,
    "log": cmd_log
}


def dump_projects(xbbs):
    running = 0
    for name, proj in xbbs.projects.items():
        if not isinstance(proj.current, RunningProject):
            continue
        running += 1
        log.info("project {} running: {}", name, proj.current)
    log.info("running {} project(s)", running)


def main():
    global log
    StderrHandler().push_application()
    log = Logger("xbbs.coordinator")

    XBBS_CFG_DIR = os.getenv("XBBS_CFG_DIR", "/etc/xbbs")
    with open(path.join(XBBS_CFG_DIR, "coordinator.toml"), "r") as fcfg:
        cfg = CONFIG_VALIDATOR.validate(toml.load(fcfg))

    inst = Xbbs.create(cfg)

    for name, elem in cfg["projects"].items():
        inst.projects[name] = Project(name, **elem)
        log.debug("got project {}", inst.projects[name])

    with inst.zmq.socket(zmq.REP) as sock_cmd, \
         inst.zmq.socket(zmq.PULL) as inst.intake, \
         inst.zmq.socket(zmq.PUSH) as inst.pipeline:
        inst.intake.bind(cfg["intake"]["bind"])
        for x in cfg["workers"]:
            inst.pipeline.connect(x)

        sock_cmd.bind(cfg["command_endpoint"]["bind"])
        dumper = gevent.signal_handler(signal.SIGUSR1, dump_projects, inst)
        log.info("startup")
        intake = gevent.spawn(intake_loop, inst)
        try:
            command_loop(inst, sock_cmd)
        finally:
            # XXX: This may not be the greatest way to handle this
            gevent.killall(inst.project_greenlets[:])
            gevent.kill(intake)
            dumper.cancel()

# TODO(arsen): make a clean exit
# TODO(arsen): handle the case in which workers do not successfully connect
#              and/or disconnect
