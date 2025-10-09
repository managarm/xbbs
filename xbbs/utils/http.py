# Utilities for dealing with HTTP.
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
This module contains utilities for dealing with HTTP.
"""

import typing as T

import aiohttp

from xbbs.data.config import BindLocation, get_path_host_port_from_bind_location


def aiohttp_client_session_for_bind(location: BindLocation) -> aiohttp.ClientSession:
    """
    Open a :py:class:`aiohttp.ClientSession` for a given coordinator bind location.
    """
    php = get_path_host_port_from_bind_location(location)

    if php[0] is not None:
        connector = aiohttp.UnixConnector(path=php[0])
        return aiohttp.ClientSession("http://coordinator/", connector=connector)
    else:
        T.assert_type(php[1], str)
        T.assert_type(php[2], int)
        return aiohttp.ClientSession(f"http://{php[1]}:{php[2]}/")

    T.assert_never(php)
