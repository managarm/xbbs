# Project management routes.
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
This module contains project management routes.
"""

import typing as T

from aiohttp import web

from .coordinator_state import get_coord_state

if T.TYPE_CHECKING:
    from .project import ProjectState

blueprint = web.RouteTableDef()


async def _get_project(req: web.Request) -> "ProjectState":
    """
    Get project by slug, stored as ``project_name`` in the request
    :py:class:`web.MatchInfo`.  If such a name is not found, raises ``NotFound``.
    """
    coord = get_coord_state(req.app)
    project = await coord.get_project_by_slug(req.match_info["project_name"])
    if project is None:
        raise web.HTTPNotFound()
    return project


@blueprint.get("/projects/{project_name}/start")
async def start_project(req: web.Request) -> web.Response:
    """
    Starts a given project.
    """
    coord = get_coord_state(req.app)
    proj = await _get_project(req)

    try:
        delay = float(req.query.get("delay", 0))
    except ValueError:
        raise web.HTTPBadRequest()

    new_id = await proj.run_build(coord, "increment" in req.query, delay)
    if new_id:
        return web.json_response(dict(build_id=new_id))
    else:
        return web.json_response("busy", status=429)


@blueprint.get("/projects/{project_name}/state")
async def get_project_status(req: web.Request) -> web.Response:
    """
    Return the current status of the project, including its job graph and states if a
    build is running.
    """
    proj = await _get_project(req)
    build = proj._build
    if not build:
        return web.json_response(dict(busy=False))

    return web.json_response(
        dict(
            busy=True,
            build=dict(
                build_id=build.build_id,
                node_states={k: v.name for (k, v) in build._node_states.items()},
                done_artifacts=list(build._done_artifacts),
                dag=dict(
                    nodes={
                        identifier: dict(
                            products=list(node.products),
                            dependencies=list(node.dependencies),
                            is_up_to_date=node.is_up_to_date,
                            is_unstable=node.is_unstable,
                            required_capabilities=list(node.required_capabilities),
                        )
                        for (identifier, node) in build._graph.nodes.items()
                    },
                ),
            ),
        )
    )
