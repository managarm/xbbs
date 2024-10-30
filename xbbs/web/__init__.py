# SPDX-License-Identifier: AGPL-3.0-only
import collections
import json
import os
import os.path as path
import pathlib
from datetime import datetime, timezone
from functools import wraps

import flask.json.provider
import humanize
import msgpack
import zmq
from flask import (
    Flask,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.exceptions import NotAcceptable, NotFound, ServiceUnavailable
from werkzeug.utils import safe_join

import xbbs.messages as msgs
import xbbs.util as xutils


class ExtendedJSONProvider(flask.json.provider.DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, msgs.BuildState):
            return o.name
        return super().default(o)

class BackendError(RuntimeError):
    status_code = 200

    def __init__(self, code_response):
        code, response = code_response
        self.status_code = int(code.decode("us-ascii"))
        r = msgpack.loads(response)
        super().__init__(f"Coordinator sent back code {self.status_code}, {r}")


app = Flask(__name__)
app.use_x_sendfile = os.getenv("XBBS_USE_X_SENDFILE", "").lower() in [
    "1", "t", "true", "yes",
]
app.json = ExtendedJSONProvider(app)
coordinator = os.environ["XBBS_COORDINATOR_ENDPOINT"]
projbase = os.environ["XBBS_PROJECT_BASE"]
zctx = zmq.Context.instance()


def load_build(status, proj, ts, *, adjust_small_time=True):
    base_dir = safe_join(projbase, proj, ts)
    if not path.isdir(base_dir):
        raise NotFound()
    try:
        with open(path.join(base_dir, "coordinator")) as f:
            build = json.load(f)
        if "state" in build:
            build.update(state=msgs.BuildState[build["state"]])
    except json.JSONDecodeError:
        # this is a corrupted build, ignore it
        raise NotFound()
    except FileNotFoundError:
        build = {
            "running": True,
            "jobs": {}
        }
    failures = 0
    for job in build["jobs"].values():
        jstatus = job["status"]
        if jstatus == "IGNORED_FAILURE":
            failures += 1
    build["failures"] = failures
    build["finished"] = True
    build["running"] = xutils.is_locked(base_dir, "coordinator", status.pid)
    if "run_time" not in build:
        build["finished"] = False
    elif build["run_time"] < 1 and adjust_small_time:
        build["run_time"] = "no time"
    build["base_dir"] = base_dir
    return build


def load_job(status, proj, ts, job):
    # XXX: the names here are a mess
    exists = False
    job_info = {
        "exit_code": -1.0
    }
    projdir = safe_join(projbase, proj, ts)
    if not path.isdir(projdir):
        raise NotFound()
    try:
        with open(path.join(projdir, f"{job}.info")) as info:
            job_info = json.load(info)
        exists = True
    except FileNotFoundError:
        pass
    if "run_time" not in job_info:
        job_info["run_time"] = "unknown time"
    elif job_info["run_time"] < 1:
        job_info["run_time"] = "no time"
    job_info.update(
        running=not exists,
        finished=exists,
        success=job_info["exit_code"] == 0
    )
    coord_is_running = xutils.is_locked(projdir, "coordinator", status.pid)
    if not coord_is_running:
        job_info["running"] = False
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
        # this should be configurable
        if not rsock.poll(timeout=1500):
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
    for project_name in status.projects:
        project = path.join(projbase, project_name)
        # I don't want deeper nesting code
        try:
            _listdir = os.listdir(project)
        except NotADirectoryError:
            continue
        if path.isdir(path.join(project, "rolling/package_repo")):
            status.projects[project_name].update(rolling_pkgs=True)
        for build in _listdir:
            try:
                build_ts = xutils.strptime(build, xutils.TIMESTAMP_FORMAT)
            except ValueError:
                continue
            build_info = load_build(status, project_name, build)
            build_info.update(
                timestamp=build_ts,
                project=project_name,
                log=url_for("show_log_list", proj=project_name, ts=build)
            )
            if "jobs" in build_info:
                build_info.update(
                    jobs=url_for("job_view", proj=project_name, ts=build)
                )
                if path.isdir(path.join(project, build, "package_repo")):
                    url = url_for("package_list",
                                  proj=project_name,
                                  ts=build
                                  )
                    build_info.update(pkgrepo=url)
                if path.isdir(path.join(project, build, "file_repo")):
                    url = url_for("file_list",
                                  proj=project_name,
                                  ts=build
                                  )
                    build_info.update(filerepo=url)

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
    build_info = load_build(status, proj, ts)
    for k, v in build_info["jobs"].items():
        if os.access(path.join(build_info["base_dir"], f"{k}.log"), os.R_OK):
            v.update(log=url_for("show_log", proj=proj, ts=ts, job=k))
        v.update(status=msgs.JobStatus[v["status"]])

    grouped_jobs = collections.OrderedDict()
    grouped_jobs[msgs.JobStatus.FAILED] = collections.OrderedDict()
    grouped_jobs[msgs.JobStatus.PREREQUISITE_FAILED] = \
        collections.OrderedDict()
    grouped_jobs[msgs.JobStatus.RUNNING] = collections.OrderedDict()
    for x in reversed(msgs.JobStatus):
        if x in grouped_jobs:
            continue  # fail first
        grouped_jobs[x] = collections.OrderedDict()

    def _sort_key(x):
        _name, data = x
        kind, _, name = _name.partition(":")
        if not name:
            name = kind
            kind = ""
        return (kind, name)

    for k, v in sorted(build_info["jobs"].items(), key=_sort_key):
        vstatus = v["status"]
        if any(x["failed"] for x in v["deps"]):
            vstatus = msgs.JobStatus.PREREQUISITE_FAILED
        grouped_jobs[vstatus][k] = v
    return render_template("jobs.html",
                           load=status.load,
                           host=status.hostname,
                           build=build_info,
                           grouped_jobs=grouped_jobs,
                           ts=ts,
                           project=proj,
                           job_count=sum(len(x) for x in grouped_jobs.values())
                           )


@app.route("/logs/<proj>/<ts>/<job>")
def show_log(proj, ts, job):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    build = load_job(status, proj, ts, job)
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
    build = load_build(status, proj, ts)
    builddir = safe_join(projbase, proj, ts)
    try:
        for x in os.listdir(builddir):
            if not x.endswith(".log"):
                continue
            x = x[:-4]
            obj = load_job(status, proj, ts, x)
            obj.update(
                job=x,
                link=url_for("show_log", proj=proj, ts=ts, job=x)
            )
            jobs.append(obj)
    except FileNotFoundError as e:
        raise NotFound() from e
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
    # XXX: check if coordinator is online?
    logdir = safe_join(projbase, proj, ts)
    return send_from_directory(logdir, f"{job}.log",
                               mimetype="text/plain")


@app.template_filter("humanizedelta")
def humanize_delta(x):
    if isinstance(x, str):
        return x
    if x < 1:
        return "no time"
    return humanize.precisedelta(x)


@app.template_filter("humanizesize")
def humanize_size(x):
    return humanize.naturalsize(x, binary=True, gnu=True)


@app.template_filter("humanizeiso")
def parse_and_humanize_iso(iso, *args, **kwargs):
    try:
        if isinstance(iso, str):
            iso = xutils.strptime(iso, xutils.TIMESTAMP_FORMAT)

        if iso.tzinfo is not None and iso.tzinfo.utcoffset(iso) is not None:
            # this doesn't make a naive object without the replace
            iso = iso.astimezone().replace(tzinfo=None)
        return humanize.naturaltime(iso, *args, **kwargs)
    except ValueError:
        raise NotFound()


@app.template_filter("formatts")
def format_timestamp(x):
    dt = datetime.fromtimestamp(x, tz=timezone.utc).strftime("%d-%b-%Y %H:%m")
    return dt


@app.template_global()
def xbps_parse(x):
    _, check, ver = x.rpartition("-")
    if not check:
        raise RuntimeError(f"invalid pkgver {x}")
    return xutils.xbps_parse(ver)


def find_latest_build(status, proj, **kwargs):
    project = safe_join(projbase, proj)
    try:
        _listdir = os.listdir(project)
    except NotADirectoryError:
        raise NotFound()

    # XXX: could have been max() in python3.8 but the current target, 3.6, is
    # missing the walrus operator
    # XXX: preferably use some symlink thing to speed this process up?
    latest_build_info = None
    latest_build_dt = None
    latest_build_ts = None
    for x in _listdir:
        try:
            dt = xutils.strptime(x, xutils.TIMESTAMP_FORMAT)
        except ValueError:
            continue
        bi = load_build(status, proj, x, **kwargs)
        if not bi.get("success", False):
            continue
        if not latest_build_dt or dt > latest_build_dt:
            latest_build_dt = dt
            latest_build_ts = x
            latest_build_info = bi

    if not latest_build_dt:
        raise NotFound()

    return (latest_build_info, latest_build_ts)


def render_pkgs_for_builds(status, proj, ts, build_info):
    # TODO: multiarch
    pkg_repo_dir = pathlib.Path(projbase) / proj / ts / "package_repo/"
    distrib_repo = pathlib.Path(projbase) / proj / "distrib/package_repo/"
    repodata_files = [x for x in pkg_repo_dir.iterdir()
                      if str(x).endswith("-repodata")]
    if len(repodata_files) > 1:
        raise RuntimeError("multiarch builds unsupported")
    if len(repodata_files) == 0:
        raise NotFound()

    pkg_idx = xutils.read_xbps_repodata(repodata_files[0])
    diff_idx = None
    try:
        diff_idx = distrib_repo / repodata_files[0].name
        diff_idx = xutils.read_xbps_repodata(diff_idx)
    except FileNotFoundError:
        # we assume no diff by default
        diff_idx = None
    return render_template("packages.html",
                           load=status.load,
                           host=status.hostname,
                           repodata=pkg_idx,
                           diff_repodata=diff_idx,
                           project=proj,
                           ts=ts,
                           build_info=build_info
                           )


@app.route("/project/<proj>/files")
@app.route("/project/<proj>/files/<ts>")
def file_list(proj, ts="latest"):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    if ts == "latest":
        (build_info, ts) = find_latest_build(status, proj)
    else:
        build_info = load_build(status, proj, ts)

    file_repo = safe_join(projbase, proj, ts, "file_repo")
    with os.scandir(file_repo) as it:
        dirscan = list(it)

    dirscan.sort(key=lambda x: x.name)
    return render_template("files.html",
                           load=status.load,
                           host=status.hostname,
                           project=proj,
                           ts=ts,
                           build_info=build_info,
                           files=dirscan
                           )


@app.route("/project/<proj>/packages")
@app.route("/project/<proj>/packages/<ts>")
def package_list(proj, ts="latest"):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    if ts == "latest":
        (build_info, ts) = find_latest_build(status, proj)
    elif ts == "rolling":
        build_info = {}
    else:
        build_info = load_build(status, proj, ts)
    return render_pkgs_for_builds(status, proj, ts, build_info)


@app.route("/repos/packages/<proj>/<ts>/<filename>")
def dl_package(proj, ts, filename):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    if ts == "latest":
        (info, ts) = find_latest_build(status, proj)
    pkgf = safe_join(projbase, proj, ts, "package_repo")
    return send_from_directory(pkgf, filename, as_attachment=True)


@app.route("/repos/tools/<proj>/<ts>/<filename>")
def dl_tool(proj, ts, filename):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    if ts == "latest":
        (info, ts) = find_latest_build(status, proj)
    pkgf = safe_join(projbase, proj, ts, "tool_repo")
    return send_from_directory(pkgf, filename, as_attachment=True)


@app.route("/repos/files/<proj>/<ts>/<filename>")
def dl_file(proj, ts, filename):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    if ts == "latest":
        (info, ts) = find_latest_build(status, proj)
    pkgf = safe_join(projbase, proj, ts, "file_repo")
    return send_from_directory(pkgf, filename, as_attachment=True)


# XXX: the following bits follow a yet-to-be-stabilized future URL convention.
#      the rest of the project should be updated to follow it at some point. DO
#      NOT forget to update this.

@app.route("/projects/<proj>/builds")
def show_build_list_json(proj):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    bm = request.accept_mimetypes.best_match(["text/html", "application/json"])
    if bm == "application/json":
        builds = {}
        _listdir = os.listdir(safe_join(projbase, proj))
        for build in _listdir:
            try:
                xutils.strptime(build, xutils.TIMESTAMP_FORMAT)
            except ValueError:
                continue
            loaded_build = load_build(status, proj, build,
                                      adjust_small_time=False)
            builds[build] = {
                k: v for k, v in loaded_build.items()
                if k not in ["jobs", "base_dir"]
            }
        return jsonify(builds)
    else:
        # TODO(arsen): add HTML handling here
        raise NotAcceptable()


@app.route("/projects/<proj>/latest")
@app.route("/projects/<proj>/<ts>")
def show_build_json(proj, ts="latest"):
    status = msgs.StatusMessage.unpack(send_request(b"status", b""))
    bm = request.accept_mimetypes.best_match(["text/html", "application/json"])
    if bm == "application/json":
        if ts == "latest":
            (info, ts) = find_latest_build(status, proj,
                                           adjust_small_time=False)
        else:
            info = load_build(status, proj, ts, adjust_small_time=False)
        # TODO(arsen): add download url to artifacts?
        return jsonify({k: v for k, v in info.items() if k != "base_dir"})
    else:
        # TODO(arsen): add HTML handling here
        raise NotAcceptable()
