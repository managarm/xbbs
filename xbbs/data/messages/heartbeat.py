# Heartbeat messages.
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
This module contains heartbeat messages exchanged between the worker and coordinator.
"""

import os
import socket
import typing as T

from pydantic import BaseModel


class WorkerHeartbeat(BaseModel):
    """
    A heartbeat sent from the worker to the coordinator periodically.
    """

    msg_type: T.Literal["WHB"]

    hostname: str
    """
    Worker hostname.  Not used for identification, just as a display name to the
    administrator.
    """

    load_avg: tuple[float, float, float]
    """
    Load average in the last 1, 10 and 15 minutes.

    See also :py:func:`os.getloadavg`.
    """

    @classmethod
    def make_for_current_machine(cls) -> "WorkerHeartbeat":
        return WorkerHeartbeat(
            msg_type="WHB", hostname=socket.gethostname(), load_avg=os.getloadavg()
        )
