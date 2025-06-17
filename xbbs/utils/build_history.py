# Utilities for working with the build history of a project.
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
This module contains functions used to work with project build histories.
"""

import os
import os.path as path

import xbbs.utils.ts as xbu_ts


def get_project_dir(coordinator_root: str, project_slug: str) -> str:
    """Get the directory in which ``project_slug`` is stored."""
    return path.join(coordinator_root, "projects", project_slug)


def get_project_builds(coordinator_root: str, project_slug: str) -> list[str]:
    """
    For the given ``project_slug``, find the builds this project has in ``coordinator_root``.
    """
    try:
        project_dir = get_project_dir(coordinator_root, project_slug)
        return list(filter(xbu_ts.parse_build_id_into_ts, os.listdir(project_dir)))
    except FileNotFoundError:
        return []
