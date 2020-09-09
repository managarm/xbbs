import gevent.monkey
if not gevent.monkey.is_module_patched("socket"):
    gevent.monkey.patch_all()
from datetime import datetime
from flask import Flask, render_template, url_for, safe_join, make_response, \
                  send_from_directory
from functools import wraps
from gevent.fileobject import FileObjectThread
import json
import humanize
import msgpack
import os
import os.path as path
import plistlib
import tarfile
import valideer as V
from werkzeug.exceptions import NotFound, ServiceUnavailable
import xbbs.messages as msgs
import xbbs.util as xutils
import zmq.green as zmq
import zstandard


app = Flask(__name__)
coordinator = os.environ["XBBS_COORDINATOR_ENDPOINT"]
projbase = os.environ["XBBS_PROJECT_BASE"]
zctx = zmq.Context.instance()

if not coordinator:
    raise ValueError("XBBS_COORDINATOR_ENDPOINT not set")
if not projbase:
    raise ValueError("XBBS_PROJECT_BASE not set")


class BackendError(RuntimeError):
    status_code = 200

    def __init__(self, code_response):
        code, response = code_response
        self.status_code = int(code.decode("us-ascii"))
        r = msgpack.loads(response)
        super().__init__(f"Coordinator sent back code {self.status_code}, {r}")


def load_build(proj, ts):
    base_dir = safe_join(projbase, proj, ts)
    try:
        with open(path.join(base_dir, f"coordinator")) as f:
            build = json.load(f)
    except FileNotFoundError:
        build = {
            "running": True,
        }
    if not "run_time" in build:
        build["running"] = True
    elif build["run_time"] < 1:
        build["run_time"] = "no time"
    build["base_dir"] = base_dir
    return build


def load_job(proj, ts, job):
    exists = False
    exit_code = -1
    job_info = {
        "exit_code": -1.0
    }
    projdir = path.join(projbase, proj, ts)
    if not path.isdir(projdir):
        raise NotFound()
    try:
        with open(path.join(projdir, f"{job}.info")) as info:
            job_info = json.load(info)
        exists = True
    except FileNotFoundError as e:
        pass
    if not "run_time" in job_info:
        job_info["run_time"] = "unknown time"
    elif job_info["run_time"] < 1:
        job_info["run_time"] = "no time"
    job_info.update(
        running=not exists,
        success=job_info["exit_code"] == 0
    )
    return job_info


def no_cache(x):
    @wraps(x)
    def wrapper(*args, **kwargs):
        resp = make_response(x(*args, **kwargs))
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return wrapper


def send_request(cmd, arg):
    with zctx.socket(zmq.REQ) as rsock:
        rsock.connect(coordinator)
        rsock.send_multipart([cmd, arg])
        if rsock.poll(1500) == 0:
            raise ServiceUnavailable()
        status, content = rsock.recv_multipart()
        # we only expect 200 from the server for a status request, because it's
        # a non empty success
        if status != b"200":
            raise BackendError((status, content))
        return content


@app.errorhandler(BackendError)
def handle_backend_error(e):
    # TODO(arsen): render html
    return f"{type(e).__name__}: {e}", e.status_code


@app.route("/")
def overview():
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    build_history = []
    for project_name in os.listdir(projbase):
        if not xutils.PROJECT_REGEX.match(project_name):
            continue
        project = path.join(projbase, project_name)
        for build in os.listdir(project):
            try:
                build_ts = datetime.strptime(build, xutils.TIMESTAMP_FORMAT)
            except ValueError:
                continue
            build_info = load_build(project_name, build)
            build_info.update(
                timestamp=build_ts,
                project=project_name,
                log=url_for("show_log_list", proj=project_name, ts=build)
            )
            if "jobs" in build_info:
                build_info.update(
                    jobs=url_for("job_view", proj=project_name, ts=build)
                )
            build_history.append(build_info)
    build_history.sort(key=lambda x: x["timestamp"], reverse=True)
    return render_template("overview.html",
                           projects=status.projects,
                           load=status.load,
                           host=status.hostname,
                           history=build_history
                          )


@app.route("/jobs/<proj>/<ts>")
def job_view(proj, ts):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    build_info = load_build(proj, ts)
    for k, v in build_info["jobs"].items():
        if os.access(path.join(build_info["base_dir"], f"{k}.log"), os.R_OK):
            v.update(log=url_for("show_log", proj=proj, ts=ts, job=k))
    return render_template("jobs.html",
                           load=status.load,
                           host=status.hostname,
                           build=build_info,
                           ts=ts,
                           project=proj
                          )


@app.route("/logs/<proj>/<ts>/<job>")
def show_log(proj, ts, job):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    logfile = safe_join(projbase, proj, ts, f"{job}.log")
    build = load_job(proj, ts, job)
    return render_template("log.html",
                           project=proj,
                           ts=ts,
                           job=job,
                           load=status.load,
                           host=status.hostname,
                           build=build,
                           raw=url_for("show_raw_log",
                                       proj=proj, ts=ts,
                                       job=job)
                          )

@app.route("/logs/<proj>/<ts>/")
def show_log_list(proj, ts):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    jobs = []
    build = load_build(proj, ts)
    builddir = safe_join(projbase, proj, ts)
    for x in os.listdir(builddir):
        if not x.endswith(".log"):
            continue
        x = x[:-4]
        obj = load_job(proj, ts, x)
        obj.update(
            job=x,
            link=url_for("show_log", proj=proj, ts=ts, job=x)
        )
        jobs.append(obj)
    jobs.sort(key=lambda x: x["running"], reverse=True)
    return render_template("loglist.html",
                           project=proj,
                           ts=ts,
                           load=status.load,
                           host=status.hostname,
                           jobs=jobs,
                           build=build
                          )


@app.route("/logs/raw/<proj>/<ts>/<job>")
@no_cache
def show_raw_log(proj, ts, job):
    logdir = safe_join(projbase, proj, ts)
    return send_from_directory(logdir, f"{job}.log")


def _read_repodata(ridx):
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


@app.route("/project/<proj>/packages")
def show_pkg_repo(proj):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    # TODO(arsen): architecture
    ridx = safe_join(projbase, proj, f"package_repo", "x86_64-repodata")
    if not path.exists(ridx):
        # TODO(arsen): tell the user there's no repo (yet)
        raise NotFound()
    pkg_idx = gevent.get_hub().threadpool.spawn(_read_repodata, ridx).get()
    return render_template("packages.html",
            load=status.load,
            host=status.hostname,
            repodata=pkg_idx,
            project=proj
    )


@app.route("/project/<proj>/repo/<filename>")
def dl_package(proj, filename):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    # TODO(arsen): architecture
    pkgf = safe_join(projbase, proj, f"package_repo")
    return send_from_directory(pkgf, filename, as_attachment=True)


app.jinja_env.filters["humanizedelta"] = humanize.precisedelta


@app.template_filter("humanizesize")
def humanize_size(x):
    return humanize.naturalsize(x, binary=True, gnu=True)


@app.template_filter("humanizeiso")
def parse_and_humanize_iso(iso, *args, **kwargs):
    try:
        if type(iso) == str:
            iso = datetime.strptime(iso, xutils.TIMESTAMP_FORMAT)
        return humanize.naturaltime(iso, *args, **kwargs)
    except ValueError:
        raise NotFound()
