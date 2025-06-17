# Project viewing routes.
# Copyright (C) 2025  Arsen Arsenović <arsen@managarm.org>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import itertools
import os.path as path
import sqlite3
import typing as T

from flask import Blueprint, Response, g, render_template, request, send_from_directory
from werkzeug.exceptions import NotFound

import xbbs.coordinator.build_state as xbc_b
import xbbs.utils.build_history as xbu_h
from xbbs.buildsystem.dag import NodeState
from xbbs.web.config import get_coordinator_work_root
from xbbs.web.utils import extract_current_page, get_page_number

bp = Blueprint("project", __name__)


def _open_build(build_dir: str) -> sqlite3.Connection:
    try:
        return xbc_b.open_build_db(build_dir)
    except sqlite3.OperationalError as e:
        if e.sqlite_errorname != "SQLITE_CANTOPEN":
            raise
        raise NotFound()


def _check_slug(slug: str) -> None:
    """Validate that ``slug`` exists as a project slug."""
    if slug not in g.status.projects:
        raise NotFound()


@bp.before_request
def _inject_page_number() -> None:
    g.page_number = get_page_number()


@bp.get("/<project_slug:slug>")
@bp.get("/<project_slug:slug>/")
def history_and_overview(slug: str) -> str:
    _check_slug(slug)
    work_root = get_coordinator_work_root()
    builds = sorted(extract_current_page(xbu_h.get_project_builds(work_root, slug)), reverse=True)

    def _read_build(build: str) -> tuple[str, xbc_b.DbBuild]:
        conn = _open_build(path.join(xbu_h.get_project_dir(work_root, slug), build))
        try:
            return (build, xbc_b.read_build_object(conn))
        finally:
            conn.close()

    return render_template("project.html", slug=slug, builds=list(_read_build(b) for b in builds))


@bp.get("/<project_slug:slug>/<build_id:build>/logs/<execution_id:execution>")
@bp.get("/<project_slug:slug>/<build_id:build>/logs/coordinator")
def show_log(slug: str, build: str, execution: str = "coordinator") -> str:
    _check_slug(slug)

    work_root = get_coordinator_work_root()
    if execution != "coordinator":
        conn = _open_build(path.join(xbu_h.get_project_dir(work_root, slug), build))
        try:
            read_execution = xbc_b.read_one_execution(conn, execution)
        finally:
            conn.close()

        if read_execution is None:
            raise NotFound()
    else:
        read_execution = None

    return render_template(
        "project_log_view.html",
        slug=slug,
        build=build,
        log_name=f"{read_execution.node_id} ({execution})" if read_execution else "Coordinator",
        execution=read_execution,
        raw_exec=execution,
    )


@bp.get("/<project_slug:slug>/<build_id:build>/logs/<execution_id:execution>/raw")
@bp.get("/<project_slug:slug>/<build_id:build>/logs/coordinator/raw")
def raw_log(slug: str, build: str, execution: str = "coordinator") -> Response:
    _check_slug(slug)

    work_root = get_coordinator_work_root()
    log_dir = path.join(xbu_h.get_project_dir(work_root, slug), build, "logs")
    return send_from_directory(log_dir, f"{execution}.log")


@bp.get("/<project_slug:slug>/<build_id:build>/logs")
def log_list(slug: str, build: str) -> str:
    # No need for pagination on this one, since the user is likely to want all logs, and they
    # require a single query to find, and contain very little data.
    _check_slug(slug)
    node_id = request.args.get("node", None)

    work_root = get_coordinator_work_root()
    conn = _open_build(path.join(xbu_h.get_project_dir(work_root, slug), build))
    try:
        all_executions = xbc_b.read_executions(conn, node_id)
        build_obj = xbc_b.read_build_object(conn)
    finally:
        conn.close()

    return render_template(
        "project_log_list.html",
        slug=slug,
        build=build,
        all_executions=all_executions,
        node_id=node_id,
        build_obj=build_obj,
    )


@bp.get("/<project_slug:slug>/<build_id:build>/jobs")
def job_graph(slug: str, build: str) -> str:
    _check_slug(slug)

    work_root = get_coordinator_work_root()
    conn = _open_build(path.join(xbu_h.get_project_dir(work_root, slug), build))
    try:
        graph = xbc_b.read_job_graph(conn)
        build_obj = xbc_b.read_build_object(conn)
    finally:
        conn.close()

    def job_grouper(job: xbc_b.DbJob) -> int:
        state = job.state
        match state:
            case NodeState.FAILED:
                # Most interesting case.
                return 10
            case NodeState.FAILED_ABNORMALLY:
                # Interesting to the admin
                return 20

            # The rest of these mostly don't matter, but the template expects them to be grouped.
            case NodeState.SUCCEEDED:
                return 40
            case NodeState.READY:
                return 50
            case NodeState.WAITING:
                return 80
            case NodeState.UNSATISFIABLE:
                return 90
            case NodeState.UP_TO_DATE:
                # Dead last.  Least interesting.
                return 100

        T.assert_never(state)

    def get_job_state(job: xbc_b.DbJob) -> NodeState:
        return job.state

    job_groups = itertools.groupby(sorted(graph.jobs.values(), key=job_grouper), key=get_job_state)

    return render_template(
        "project_job_graph.html",
        slug=slug,
        build=build,
        artifacts=graph.artifacts,
        build_obj=build_obj,
        job_groups={g: list(j) for (g, j) in job_groups},
    )


@bp.get("/<project_slug:slug>/<build_id:build>/repo/<path:repo_file>")
def get_repo_file(slug: str, build: str, repo_file: str) -> Response:
    _check_slug(slug)

    work_root = get_coordinator_work_root()
    repo_dir = path.join(xbu_h.get_project_dir(work_root, slug), build, "repo")

    return send_from_directory(repo_dir, repo_file)
