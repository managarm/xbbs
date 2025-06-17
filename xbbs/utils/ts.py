# Time functions.
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
This module contains timestamp-related functions.
"""

import datetime
import re

BUILD_ID_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
BUILD_ID_RE = re.compile(r"^\d\d\d\d-\d\d-\d\dT\d\d:\d\d:\d\d$")


def get_build_id_for_now() -> str:
    """Construct a build ID for the current time."""
    now = datetime.datetime.now(datetime.UTC)
    return now.strftime(BUILD_ID_TS_FORMAT)


def parse_build_id_into_ts(build_id: str) -> datetime.datetime | None:
    """
    Given a string, if it is a build ID, parse it into a TZ-aware datetime.

    Returns:
      The timestamp of this build, or ``None`` if the parse failed.
    """
    try:
        return datetime.datetime.fromisoformat(build_id).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
