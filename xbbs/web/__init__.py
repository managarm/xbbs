# Read-only web frontend for xbbs.
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

"""
Root of the ``xbbs-web`` frontend.
"""

import logging
import os.path as path
from datetime import datetime, timedelta, timezone

import humanize
import markupsafe
import requests
from flask import Flask, current_app, g
from werkzeug.exceptions import ServiceUnavailable
from werkzeug.routing import BaseConverter

import xbbs.utils.ts as xbu_ts
from xbbs.buildsystem.dag import NodeState
from xbbs.coordinator.build_state import ArtifactState, BuildState
from xbbs.coordinator.execution import ExecutionState
from xbbs.data.coordinator.status import CoordinatorStatus

logger = logging.getLogger(__name__)


class SlugConverter(BaseConverter):
    """
    Handle validating project slugs from URL varables.
    """

    regex = r"^[a-zA-Z0-9_]+$"


class BuildIdConverter(BaseConverter):
    """
    Handle validating build IDs from URL varables.
    """

    regex = xbu_ts.BUILD_ID_RE.pattern


class ExecutionIdConverter(BaseConverter):
    """
    Handle validating execution IDs from URL varables.
    """

    regex = r"^[a-zA-Z0-9-_]{22}$"


def get_coord_status() -> None:
    """Before each request, ask the coordinator for status."""
    try:
        url = current_app.config.get("COORDINATOR_URL")
        if not isinstance(url, str):
            raise RuntimeError("COORDINATOR_URL misconfigured")
        status_json = requests.get(url).json()
        status = CoordinatorStatus.validate(status_json)
    except Exception:
        logger.exception("cannot contact coordinator")
        raise ServiceUnavailable()

    g.status = status


def humanize_as_delta(x: datetime) -> str:
    """Humanize a TZ-aware timezone as a '... ago' string."""
    now = datetime.now(tz=timezone.utc)
    delta = now - x
    return humanize.naturaltime(delta)


_STATE_NAMES = {
    ArtifactState.MISSING: "Missing",
    ArtifactState.RECEIVED: "Received",
    ArtifactState.UP_TO_DATE: "Already up to date",
    ArtifactState.WAITING: "Waiting",
    NodeState.FAILED: "Failed to build",
    NodeState.FAILED_ABNORMALLY: "Failed abnormally",
    NodeState.READY: "Ready to build",
    NodeState.SUCCEEDED: "Successfully built",
    NodeState.UNSATISFIABLE: "Unsatisfiable",
    NodeState.WAITING: "Waiting for dependencies",
    NodeState.UP_TO_DATE: "Already up to date",
    BuildState.CALCULATING: "Calculating node graph",
    BuildState.COORDINATOR_FAILED: "Failed due to a coordinator error",
    BuildState.FAILED: "Failed to complete",
    BuildState.FETCHING: "Downloading distro source code",
    BuildState.RUNNING: "Running",
    BuildState.SCHEDULED: "Scheduled to start soon",
    BuildState.SETUP: "Preparing the build root",
    BuildState.SUCCEEDED: "Successfully built",
    BuildState.UPDATING_MIRRORS: "Updating repository mirrors",
    ExecutionState.FAILED: "Failed",
    ExecutionState.FAILED_ABNORMALLY: "Failed abnormally",
    ExecutionState.IN_QUEUE: "In job queue",
    ExecutionState.RUNNING: "Running",
    ExecutionState.SUCCEEDED: "Successfully built",
}
assert all(
    state in _STATE_NAMES
    for states in (ArtifactState, NodeState, BuildState, ExecutionState)
    for state in states
), "Some state is not in _STATE_NAMES"


def render_state(
    state: ArtifactState | NodeState | BuildState | ExecutionState,
) -> markupsafe.Markup:
    """Given a state, produces a human-readable name for it."""

    if isinstance(state, NodeState):
        # Green if both, red if final, otherwise, default.
        color = ["", "text-red-500", "text-green-500"][state.is_final + state.is_success]
    elif state in {
        ExecutionState.FAILED,
        ExecutionState.FAILED_ABNORMALLY,
        BuildState.FAILED,
        BuildState.COORDINATOR_FAILED,
        ArtifactState.MISSING,
    }:
        color = "text-red-500"
    elif state in {
        ExecutionState.SUCCEEDED,
        BuildState.SUCCEEDED,
        ArtifactState.RECEIVED,
        ArtifactState.UP_TO_DATE,
    }:
        color = "text-green-500"
    else:
        color = ""

    color = markupsafe.escape(color)
    return markupsafe.Markup(
        f'<span class="{color}">{markupsafe.escape(_STATE_NAMES[state])}</span>'
    )


def render_state_colorless(state: ArtifactState | NodeState | BuildState | ExecutionState) -> str:
    return _STATE_NAMES[state]


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_prefixed_env("XBBS")

    app.url_map.converters["project_slug"] = SlugConverter
    app.url_map.converters["build_id"] = BuildIdConverter
    app.url_map.converters["execution_id"] = ExecutionIdConverter

    work_root = app.config.get("COORDINATOR_WORK_ROOT")
    if not work_root or not path.isdir(work_root):
        raise RuntimeError("COORDINATOR_WORK_ROOT must be a directory")

    # Add humanize stuff.
    app.add_template_filter(humanize_as_delta)
    app.add_template_filter(
        lambda secs: humanize.naturaldelta(timedelta(seconds=secs)), name="humanize_seconds_delta"
    )
    app.add_template_filter(humanize.naturaltime)
    app.add_template_filter(humanize.naturaldelta)
    app.add_template_filter(render_state)
    app.add_template_filter(render_state_colorless)

    app.before_request(get_coord_status)

    from .views import overview

    app.register_blueprint(overview.bp)

    from .views import project

    app.register_blueprint(project.bp, url_prefix="/projects")

    return app
