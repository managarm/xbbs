# Coordinator entrypoint.
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

from aiohttp import web

import xbbs.data.config as config
import xbbs.utils.logging as xbu_logging

from .coordinator_state import COORDINATOR_STATE_KEY, CoordinatorState


def main() -> None:
    coordinator_config = config.load_and_validate_config(
        "coordinator.toml", config.CoordinatorConfig
    )
    xbu_logging.apply_logging_config(coordinator_config.log)

    app = web.Application()
    app[COORDINATOR_STATE_KEY] = CoordinatorState(coordinator_config)

    (path, host, port) = coordinator_config.get_path_host_port()

    # Register route tables.
    from . import worker_socket

    app.add_routes(worker_socket.blueprint)

    from . import admin_routes

    app.add_routes(admin_routes.blueprint)

    from . import project_routes

    app.add_routes(project_routes.blueprint)

    from . import status_routes

    app.add_routes(status_routes.blueprint)

    web.run_app(
        app,
        path=path,
        host=host,
        port=port,
        # Used only for some silly banner.
        print=None,
    )
