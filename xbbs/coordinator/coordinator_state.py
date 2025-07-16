# Global state associated with xbbs
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
This module holds a class responsible for holding state independent of projects and
builds, but used by them.
"""

import asyncio
import os.path as path
import typing as T

from aiohttp import web

from xbbs.data.coordinator.status import WorkerStatus

from .enqueued_task import EnqueuedTask
from .execution import Execution, generate_execution_id
from .project import ProjectState
from .task_queue import TaskQueue
from .worker import WorkerTracker

if T.TYPE_CHECKING:
    import xbbs.data.config as config
    from xbbs.data.messages.tasks import TaskResponse

    from .project import Build

# TypeVars (remove this blot when Debian gets 3.12)
R = T.TypeVar("R")


class CoordinatorState:
    """
    Global state used across all projects and builds.
    """

    def __init__(self, coordinator_config: "config.CoordinatorConfig") -> None:
        self.build_tasks: T.Final = set[asyncio.Task[T.Any]]()
        """
        Group storing all build-related tasks.
        """
        self.config: T.Final = coordinator_config
        """
        Configuration values this coordinator was started with.
        """

        def _proj_dir(slug: str) -> str:
            return path.join(coordinator_config.work_root, "projects", slug)

        self._project_state: T.Final = {
            slug: ProjectState(slug, project, _proj_dir(slug))
            for (slug, project) in coordinator_config.projects.items()
        }

        self.collection_directory = path.join(coordinator_config.work_root, ":collect")
        """Directory in which to collect uploaded files temporarily."""

        self._worker_tracker_lock: T.Final = asyncio.Lock()
        self._worker_trackers: T.Final = dict[int, WorkerTracker]()
        self._worker_next_id = 1

        self.outgoing_task_queue = TaskQueue[EnqueuedTask]()

        self._execution_lock: T.Final = asyncio.Lock()
        self._execution_table: T.Final = dict[str, Execution]()

    def add_build_task(self, task: T.Coroutine[T.Any, T.Any, R]) -> asyncio.Task[R]:
        """
        Saves a task into the build task group, so that it may be shut down gracefully.
        """
        t = asyncio.create_task(task)
        self.build_tasks.add(t)
        t.add_done_callback(self.build_tasks.discard)
        return t

    # Worker tracking.
    async def allocate_worker_tracker(self, ws: "web.WebSocketResponse") -> WorkerTracker:
        """
        Returns:
          A new worker tracker object.
        """
        async with self._worker_tracker_lock:
            new_id = self._worker_next_id
            self._worker_next_id += 1
            self._worker_trackers[new_id] = tracker = WorkerTracker(self, new_id, ws)
            return tracker

    async def remove_worker(self, worker: WorkerTracker) -> None:
        """
        Removes worker from tracking, if present.  Calls ``remove_self`` on the worker.
        """
        async with self._worker_tracker_lock:
            try:
                del self._worker_trackers[worker.worker_id]
            except KeyError:
                pass
            worker.remove_self()

    async def get_worker_load(self) -> list[WorkerStatus]:
        """
        Returns a copy of the monitoring info of the currently-tracked workers.
        """
        async with self._worker_tracker_lock:
            # XXX: GIL-reliant
            return list(x.status for x in self._worker_trackers.values() if x.status)

    async def get_project_by_slug(self, slug: str) -> ProjectState | None:
        """
        Returns:
          Project identified as ``slug``, if one exists.
        """
        return self._project_state.get(slug)

    async def get_build_by_execution(self, execution: str) -> T.Optional["Build"]:
        """
        Returns:
          Build using the specified ``execution``.
        """
        async with self._execution_lock:
            try:
                return self._execution_table[execution].build
            except KeyError:
                return None

    async def add_task(self, task: "TaskResponse", capabilities: set[str], build_dir: str) -> None:
        """Enqueue a task for some worker."""
        await self.outgoing_task_queue.enqueue(
            EnqueuedTask(task=task, capabilities=capabilities, build_dir=build_dir)
        )

    # Execution tracking.
    async def alloc_execution(self, build: "Build") -> Execution:
        """
        Allocates and returns a new execution storing logs in the supplied builds log
        directory under ``${EXEC_ID}.log``.
        """
        async with self._execution_lock:
            while True:
                exec_id = generate_execution_id()
                if exec_id not in self._execution_table:
                    break
            new_exec = Execution(
                open(path.join(build.log_dir, f"{exec_id}.log"), "wb"), exec_id, build
            )

            self._execution_table[exec_id] = new_exec
            return new_exec

    def abnormally_fail_execution(self, exec_id: str) -> None:
        """
        Marks an execution as abnormally failed and removes it.
        """
        # XXX: GIL-reliant
        exec = self._execution_table.pop(exec_id, None)
        if exec is None:
            return
        exec.abnormally_fail()

    async def fail_execution(self, exec_id: str, run_time: float) -> None:
        """
        Marks an execution as abnormally failed and removes it.

        This method always removes ``exec_id``, even if it fails as a result of an
        exception when attempting to update the on-disk execution state.  Note that, in
        case of cancellation, the execution is not removed, or failed.
        """
        async with self._execution_lock:
            exec = self._execution_table.pop(exec_id, None)
            if exec is None:
                return
            exec.fail(run_time)

    async def succeed_execution(self, exec_id: str, run_time: float) -> None:
        """
        Marks an execution as successful and removes it.

        This method always removes ``exec_id``, even if it fails as a result of an
        exception when attempting to update the on-disk execution state.  Note that, in
        case of cancellation, the execution is not removed, or failed.
        """
        async with self._execution_lock:
            exec = self._execution_table.pop(exec_id, None)
            if exec is None:
                return
            exec.success(run_time)

    async def deposit_artifact_for(
        self, exec_id: str, artifact_id: str, artifact_file: str
    ) -> bool:
        """
        Deposit the given artifact into a build.  Ignores invalid ``exec_id``.

        Args:
          exec_id: Execution for which the worker is submitting an artifact.
          artifact_id: Artifact being submitted.
          artifact_file: Path to the temporary file containing the artifact.

        Returns:
          ``True`` iff the artifact was recorded for later deposition.  Caller should,
          in that case, not delete the artifact.
        """
        async with self._execution_lock:
            execution = self._execution_table.get(exec_id, None)
            if execution is None:
                return False

            # XXX: GIL-reliant
            if artifact_id in execution.received_artifacts:
                return False
            execution.received_artifacts[artifact_id] = artifact_file

        return True

    def write_log_data(self, exec_id: str, data: bytes) -> None:
        """Append ``data`` to the log for the give execution."""
        execution = self._execution_table.get(exec_id, None)
        if execution is None:
            return
        execution.log_file.write(data)
        execution.log_file.flush()


COORDINATOR_STATE_KEY = web.AppKey("coord_state", CoordinatorState)
"""
A key for storing the coordinator state in a :py:class:`web.Application`.
"""


def get_coord_state(app: web.Application) -> CoordinatorState:
    """
    Extracts the coordinator state from an :py:class:`web.Application`.  Presumes that
    it is present.
    """
    return app[COORDINATOR_STATE_KEY]
