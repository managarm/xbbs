# Routes used by xbbs-web and other frontends to see what's going on
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
Routes used for read-only introspection of the build network status.
"""

import os
import socket

from aiohttp import web

from xbbs.data.coordinator.status import CoordinatorStatus, Project

from .coordinator_state import get_coord_state
from .project import ProjectState

blueprint = web.RouteTableDef()


@blueprint.get("/")
async def get_coordinator_status(req: web.Request) -> web.Response:
    coord = get_coord_state(req.app)

    def _summarize_project(project: ProjectState) -> Project:
        return Project(
            project_name=project._project_config.name,
            description=project._project_config.description,
            classes=list(project._project_config.classes),
            running=not not project._build,
        )

    return web.json_response(
        CoordinatorStatus(
            hostname=socket.gethostname(),
            load_avg=os.getloadavg(),
            workers=await coord.get_worker_load(),
            projects={
                slug: _summarize_project(project)
                for (slug, project) in coord._project_state.items()
            },
        ).model_dump(mode="json")
    )
