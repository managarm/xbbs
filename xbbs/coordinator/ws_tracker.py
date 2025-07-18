# Helper code for tracking open WebSocket connections.
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
This module contains code that helps track active WebSocket connections and helps shut them down at
application shutdown.
"""

import asyncio
import logging
import weakref

from aiohttp import WSCloseCode, web

logger = logging.getLogger(__name__)


class WebsocketTracker:
    """
    This app tracks active WebSocket connections and handles closing them on shutdown.
    """

    def __init__(self) -> None:
        self._sockets = weakref.WeakSet[web.WebSocketResponse]()

    async def close_active_sockets(self) -> None:
        """Kill all currently active sockets."""
        for ws in set(self._sockets):
            try:
                await ws.close(code=WSCloseCode.GOING_AWAY)
            except Exception:
                logger.exception("failed to close a websocket")

    def add(self, socket: web.WebSocketResponse) -> None:
        """Add a websocket to this tracker."""
        self._sockets.add(socket)

    def remove(self, socket: web.WebSocketResponse) -> None:
        """Remove a tracked websocket, when work with it is done."""
        self._sockets.discard(socket)


WEBSOCKET_TRACKER_KEY = web.AppKey("websocket_tracker", WebsocketTracker)
