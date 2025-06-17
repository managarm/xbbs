# A few config helpers.
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

"""This module contains helpers for dealing with ``xbbs-web`` configuration."""

import typing as T

from flask import current_app


def get_coordinator_work_root() -> str:
    """Get the coordinator work root directory."""
    # Verified in __init__.py:create_app
    return T.cast(str, current_app.config.get("COORDINATOR_WORK_ROOT"))
