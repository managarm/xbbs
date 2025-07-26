# Overview page.
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

"""Overview page of ``xbbs-web``."""

import os.path as path

from flask import Blueprint, g, render_template

import xbbs.coordinator.build_state as xbc_b
import xbbs.utils.build_history as xbu_h
from xbbs.web.config import get_coordinator_work_root

bp = Blueprint("overview", __name__)


@bp.get("/")
def overview() -> str:
    # Load latest build state for overview
    last_build_and_state: dict[str, tuple[str, xbc_b.BuildState]] = {}

    for p in g.status.projects:
        assert isinstance(p, str)
        project_dir = xbu_h.get_project_dir(get_coordinator_work_root(), p)
        builds = sorted(xbu_h.get_project_builds(project_dir), reverse=True)
        for candidate in builds:
            conn = xbc_b.open_build_db(path.join(project_dir, candidate))
            try:
                build_obj = xbc_b.read_build_object(conn)
            finally:
                conn.close()

            # We already have a "running" indicator so this'd be redundant.
            if not build_obj.state.is_final:
                continue

            last_build_and_state[p] = (candidate, build_obj.state)
            break

    return render_template("overview.html", last_build_and_state=last_build_and_state)
