# SPDX-License-Identifier: AGPL-3.0-only
import gevent.monkey
gevent.monkey.patch_all()
import datetime
import gevent
import gevent.event
import attr
import attr.validators as av
from enum import Enum
from hashlib import blake2b
import json
from logbook import Logger, StderrHandler
import msgpack
import zmq.green as zmq
import os
import os.path as path
import plistlib
import re
import subprocess
import shutil
import signal
import tempfile
import time
import toml
import valideer as V
import xbbs.protocol
import xbbs.messages as msgs
import xbbs.util as xutils


def check_call_logged(cmd, **kwargs):
    log.info("running command {} (params {})", cmd, kwargs)
    return subprocess.check_call(cmd, **kwargs)


def check_output_logged(cmd, **kwargs):
    log.info("running command {} (params {})", cmd, kwargs)
    return subprocess.check_output(cmd, **kwargs)


# more properties are required than not
with V.parsing(required_properties=True, additional_properties=V.Object.REMOVE):
    @V.accepts(x=V.AnyOf("string", {"bind": "string", "connect": "string"}))
    def _receive_adaptor(x):
        if type(x) == str:
            return {"bind": x, "connect": x}
        return x

    @V.accepts(x="string")
    def _path_exists(x):
        return os.access(x, os.R_OK)

    # TODO(arsen): write an endpoint validator for workers
    CONFIG_VALIDATOR = V.parse({
        "command_endpoint": "string",
        "project_base": "string",
        "build_root": V.AllOf("string", path.isabs),
        "intake": V.AdaptBy(_receive_adaptor),
        "workers": ["string"],
        # use something like a C identifier, except disallow underscore as a
        # first character too. this is so that we have a namespace for xbbs
        # internal directories, such as collection directories
        "projects": V.Mapping(re.compile("^[a-zA-Z][_a-zA-Z0-9]{0,30}$"), {
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
    GRAPH_VALIDATOR = V.parse(V.Mapping("string", {
        "products": {"tools": ["string"], "pkgs": ["string"]},
        "needed":   {"tools": ["string"], "pkgs": ["string"]}
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
    last_run = attr.ib(default=None)

    def base(self, xbbs):
        return path.join(xbbs.project_base, self.name)

    def log(self, inst, job=None):
        tsdir = path.join(self.base(inst),
                          self.last_run.isoformat(timespec="seconds"))
        os.makedirs(tsdir, exist_ok=True)
        if not job:
            return tsdir
        return path.join(tsdir, f"{job}.log")

@attr.s
class Xbbs:
    project_base = attr.ib()
    collection_dir = attr.ib()
    tmp_dir = attr.ib()
    build_root = attr.ib()
    intake_address = attr.ib()
    running = attr.ib(default=False)
    pipeline = attr.ib(default=None)
    intake = attr.ib(default=None)
    projects = attr.ib(factory=dict)
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
    Kind = Enum("Kind", "TOOL PACKAGE")
    kind = attr.ib()
    name = attr.ib()
    received = attr.ib(default=False, eq=False, order=False)
    failed = attr.ib(default=False, eq=False, order=False)

@attr.s
class Job:
    # TODO(arsen): RUNNING is actually waiting to finish: it might say it's
    # running, but it's proobably not, and is instead stuck in the pipeline
    Status = Enum("Status", "WAITING RUNNING DONE")
    deps = attr.ib(factory=list)
    products = attr.ib(factory=list)
    status = attr.ib(default=Status.WAITING)

    def fail(self, graph):
        self.status = Job.Status.DONE

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

    tool_set = attr.ib(factory=dict)
    pkg_set = attr.ib(factory=dict)

    artifact_received = attr.ib(factory=gevent.event.Event)

    @classmethod
    def parse_graph(cls, project, revision, graph):
        graph = GRAPH_VALIDATOR.validate(graph)

        proj = cls(project.name, project.git, revision)
        tools = proj.tool_set
        pkgs = proj.pkg_set
        for job, info in graph.items():
            # TODO(arsen): circ dep detection (low prio: handled in xbstrap)
            job_val = Job()
            for x in info["needed"]["tools"]:
                if not x in tools:
                    tools[x] = Artifact(Artifact.Kind.TOOL, x)
                job_val.deps.append(tools[x])
            for x in info["needed"]["pkgs"]:
                if not x in pkgs:
                    pkgs[x] = Artifact(Artifact.Kind.PACKAGE, x)
                job_val.deps.append(pkgs[x])

            for x in info["products"]["tools"]:
                if not x in tools:
                    tools[x] = Artifact(Artifact.Kind.TOOL, x)
                job_val.products.append(tools[x])
            for x in info["products"]["pkgs"]:
                if not x in pkgs:
                    pkgs[x] = Artifact(Artifact.Kind.PACKAGE, x)
                job_val.products.append(pkgs[x])

            proj.jobs[job] = job_val

        return proj


def solve_project(inst, projinfo, project):
    while True:
        project.artifact_received.clear()
        some_waiting = False
        for name, job in project.jobs.items():
            if all([x.received for x in job.products]):
                job.status = Job.Status.DONE

            if job.status is not Job.Status.DONE:
                some_waiting = True

            if job.status is not Job.Status.WAITING:
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

            needed_tools = [x.name for x in job.deps if x.kind is Artifact.Kind.TOOL]
            needed_pkgs = [x.name for x in job.deps if x.kind is Artifact.Kind.PACKAGE]
            prod_tools = [x.name for x in job.products if x.kind is Artifact.Kind.TOOL]
            prod_pkgs = [x.name for x in job.products if x.kind is Artifact.Kind.PACKAGE]
            keys = None
            if projinfo.fingerprint:
                pubkey = path.join(projinfo.base(inst),
                                   f"{projinfo.fingerprint}.plist")
                with open(pubkey, "rb") as pkf:
                    # XXX: this is not cooperative, and should be okay because
                    # it's a small amount of data
                    keys = { projinfo.fingerprint: pkf.read() }

            job.status = Job.Status.RUNNING
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
                tool_repo=projinfo.tools,
                pkg_repo=projinfo.packages,
                xbps_keys=keys
            )
            log.debug("sending job request {}", jobreq)
            inst.pipeline.send(jobreq.pack())

        # TODO(arsen): handle the edge case in which workers are dead
        if not some_waiting:
            assert all(x.status == Job.Status.DONE for x in project.jobs.values())
            assert all(x.received for x in project.tool_set.values())
            assert all(x.received for x in project.pkg_set.values())
            return all(not x.failed for x in project.tool_set.values()) and \
                   all(not x.failed for x in project.pkg_set.values())
        project.artifact_received.wait()

# TODO(arsen): a better log collection system. It should include the output of
# git and xbstrap, too
# this should be a pty assigned to each build, on the other side of which stuff
# is read out and parsed in a manner similar to a terminal.
# the ideal would be properly rendering control sequences same way
# xterm-{256,}color does, since it is widely adopted and assumed.

def run_project(inst, project):
    # TODO(arsen): there's a race condition here. add a lock on project
    project.last_run = datetime.datetime.now()
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
    tool_repo = path.join(project.base(inst), 'tool_repo')
    package_repo = path.join(project.base(inst), 'package_repo')
    # TODO(arsen): remove to support incremental compilation
    if path.isdir(tool_repo):
        shutil.rmtree(tool_repo)
    if path.isdir(package_repo):
        shutil.rmtree(package_repo)
    with tempfile.TemporaryDirectory(dir=inst.tmp_dir) as td:
        xutils.run_hook(log, projdir, td, "pregraph")
        check_call_logged(["xbstrap", "init", projdir], cwd=td)
        graph = json.loads(check_output_logged(["xbstrap-pipeline",
                                                "compute-graph",
                                                "--artifacts", "--json"],
                                                cwd=td).decode())
        graph = GRAPH_VALIDATOR.validate(graph)
        project.current = RunningProject.parse_graph(project, rev, graph)
        try:
            start = time.monotonic()
            success = solve_project(inst, project, project.current)
            length = time.monotonic() - start
            log.info("job {} done; success? {} in {}s",
                     project.name, success, length)
            coordfile = path.join(project.log(inst), "coordinator")
            with open(coordfile, "w") as csf:
                json.dump({"success": success,
                           "run_time": length,
                           "graph": graph}, csf, indent=4)
        finally:
            project.current = None

def cmd_build(inst, name):
    "handle starting a new build on a project by name"
    name = msgpack.loads(name)
    if not name in inst.projects:
        return 404, b"unknown project"
    proj = inst.projects[name]
    if proj.current:
        return 409, b"project already running"
    gevent.spawn(run_project, inst, proj)

def command_loop(inst, sock_cmd):
    while inst.running:
        try:
            [command, arg] = sock_cmd.recv_multipart()
            command = command.decode("us-ascii")
            if not command in command_loop.cmds:
                sock_cmd.send_multipart([b"400", b"no such command"])
                continue

            code = "200"
            value = command_loop.cmds[command](inst, arg)
            if value is None:
                sock_cmd.send_multipart([b"204", b""])
                continue

            if type(value) == tuple:
                (code, value) = value
                assert type(code) == int
                code = str(code)

            sock_cmd.send_multipart([code.encode(), value])
        except zmq.ZMQError as e:
            log.exception("command loop i/o error, aborting")
            return
        except xbbs.protocol.ProtocolError as e:
            log.exception("comand processing error", e)
            sock_cmd.send_multipart([str(e.code).encode(),
                                     f"{type(e).__name__}: {e}".encode()])
        except V.ValidationError as e:
            log.exception("command processing error", e)
            sock_cmd.send_multipart([b"400",
                                     f"{type(e).__name__}: {e}".encode()])
        except Exception as e:
            log.exception("comand processing error", e)
            sock_cmd.send_multipart([b"500",
                                     f"{type(e).__name__}: {e}".encode()])


command_loop.cmds = {
    "build": cmd_build
}


def cmd_chunk(inst, value):
    chunk = msgs.ChunkMessage.unpack(value)
    if chunk.last_hash == b"initial":
        # (fd, path)
        store = tempfile.mkstemp(dir=inst.collection_dir)
        os.fchmod(store[0], 0o644)
    elif not chunk.last_hash in cmd_chunk.table:
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
        if not message.project in inst.projects:
            return
        proj = inst.projects[message.project]
        run = inst.projects[message.project].current
        if not run:
            return

        aset = run.tool_set if message.artifact_type == "tool" else run.pkg_set
        repo = path.abspath(path.join(proj.base(inst),
                            f"{message.artifact_type}_repo"))
        os.makedirs(repo, exist_ok=True)

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
            shutil.move(target, artifact_file)
            if artifact.kind == Artifact.Kind.PACKAGE:
                check_call_logged(["xbps-rindex", "-fa", artifact_file])
                maybe_sign_artifact(inst, artifact_file, proj)
        except Exception as e:
            log.exception("artifact deposit failed", e)
            artifact.failed = True

        run.artifact_received.set()
    except Exception as e:
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
    if not message.project in inst.projects:
        return
    proj = inst.projects[message.project]
    if not inst.projects[message.project].last_run:
        return

    # XXX: this is not cooperative, and should be okay because
    # it's a small amount of data
    with open(proj.log(inst, message.job), mode="a") as logfile:
        logfile.write(message.line)


def intake_loop(inst):
    while inst.running:
        try:
            [cmd, value] = inst.intake.recv_multipart()
            cmd = cmd.decode("us-ascii")
            intake_loop.cmds[cmd](inst, value)
        except zmq.ZMQError as e:
            log.exception("intake pipe i/o error, aborting")
            return
        except Exception as e:
            log.exception("intake pipe error, continuing", e)


intake_loop.cmds = {
    "chunk": cmd_chunk,
    "artifact": cmd_artifact,
    "log": cmd_log
}


def dump_projects(xbbs):
    running = 0
    for name, proj in xbbs.projects.items():
        if not proj.current:
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

        sock_cmd.bind(cfg.get("command_endpoint"))
        dumper = gevent.signal_handler(signal.SIGUSR1, dump_projects, inst)
        log.info("startup")
        intake = gevent.spawn(intake_loop, inst)
        inst.running = True
        try:
            command_loop(inst, sock_cmd)
        finally:
            inst.running = False
            intake.join()
            dumper.cancel()

# TODO(arsen): make a clean exit
# TODO(arsen): handle the case in which workers do not successfully connect
#              and/or disconnect