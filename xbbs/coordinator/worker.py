# Coordinator-side worker tracking.
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
This module contains code to maintain worker state.  This state includes its monitoring
information as well as a way to send out tasks.
"""

import asyncio
import datetime
import typing as T

from aiohttp import web

import xbbs.data.messages as xbm
from xbbs.data.coordinator.status import CurrentExecution, WorkerStatus

if T.TYPE_CHECKING:
    from .coordinator_state import CoordinatorState


class WorkerTracker:
    """
    Coordinator-side tracking of the status of a single worker.  Used as a bridge
    between the worker socket, which is constrained to receive in one task only, and the
    rest of the system, as well as a means by which to know what the worker is up to.
    """

    def __init__(
        self, coord: "CoordinatorState", worker_id: int, socket: web.WebSocketResponse
    ) -> None:
        self._lock = asyncio.Lock()
        self.worker_id: T.Final = worker_id
        self.socket: T.Final = socket
        self.removed = False
        self.coordinator_state = coord
        self.current_execution: CurrentExecution | None = None
        """
        If present, contains info about the currently active build.x
        """
        self.last_heartbeat: tuple[xbm.heartbeat.WorkerHeartbeat, datetime.datetime] | None = None
        """
        Last received heartbeat message, and when it was received.
        """

        self._task_wait_task: asyncio.Task[None] | None = None

    def remove_self(self) -> None:
        """
        Handle marking self as removed, and relinquishing any existing executions.

        Does not ensure that the worker is notified of the removal (rather, assumes the
        worker socket is already closed).
        """
        assert self.socket.closed
        if self.current_execution is not None:
            self.coordinator_state.abnormally_fail_execution(self.current_execution.execution_id)
        self.removed = True
        if self._task_wait_task is not None:
            self._task_wait_task.cancel()

    def update_status(self, heartbeat_msg: xbm.heartbeat.WorkerHeartbeat) -> None:
        # XXX: GIL-reliant
        # TODO(arsen): make the worker submit hostname on connect
        self.last_heartbeat = (heartbeat_msg, datetime.datetime.now(tz=datetime.timezone.utc))

    @property
    def status(self) -> WorkerStatus | None:
        """Return an object describing the current status of the worker."""
        if self.last_heartbeat is None:
            # Hasn't identified yet.
            return None
        (hb, ls) = self.last_heartbeat
        return WorkerStatus(
            id=self.worker_id,
            hostname=hb.hostname,
            load_avg=hb.load_avg,
            last_seen=ls,
            current_execution=self.current_execution,
        )

    async def start_wait_for_task(self, capabilities: set[str]) -> None:
        """Let this worker wait for a task that it can execute."""
        async with self._lock:
            if self.removed:
                return

            if self._task_wait_task is not None:
                return

            if self.current_execution is not None:
                return

            async def _wait_for_task() -> None:
                task = await self.coordinator_state.outgoing_task_queue.dequeue(capabilities)
                self.current_execution = CurrentExecution(
                    project_slug=task.project_slug,
                    build_id=task.build_id,
                    node_id=task.node_id,
                    execution_id=task.task.execution_id,
                )
                # TODO(arsen): handle exceptions raised here.  Currently, if an error
                # happens, an execution will simply be stuck forever (or until the
                # worker times out or disconnects, rather, even if it gets a new job in
                # the interim).
                await self.socket.send_bytes(xbm.serialize(task.task))

                from .build_state import set_exec_running

                set_exec_running(
                    task.build_dir,
                    task.task.execution_id,
                    "" if not self.status else self.status.hostname,
                )

            self._task_wait_task = twt = asyncio.create_task(_wait_for_task())

            def _clear_wait(t: asyncio.Task[None]) -> None:
                self._task_wait_task = None

            twt.add_done_callback(_clear_wait)
