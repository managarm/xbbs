# Routes for administration.
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
This module contains implementations of routes used for administrators to prod at the
coordinator.
"""


import dataclasses

from aiohttp import web

from .coordinator_state import get_coord_state

blueprint = web.RouteTableDef()


@blueprint.get("/admin/workers")
async def worker_statuses(request: web.Request) -> web.Response:
    coord = get_coord_state(request.app)
    statuses = await coord.get_worker_load()
    return web.json_response([dataclasses.asdict(status) for status in statuses])
