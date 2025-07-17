# Project state.
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
State related to projects and their jobs.
"""

import asyncio
import os
import os.path as path
import typing as T

import xbbs.utils.ts as xbu_ts
from xbbs.buildsystem.dag import Graph, NodeState

from .build_run import run

if T.TYPE_CHECKING:
    from xbbs.buildsystem import CoordinatorBuildSystem
    from xbbs.data.config import Project
    from xbbs.utils.logging.build_logger import BuildLogger

    from .coordinator_state import CoordinatorState


class ProjectState:
    """
    The project state is a monitor that simplifies executing projects and routing events
    to them.  It is responsible for:

    1. Executing a job, and only on job, after someone asks to do that.  It is required
       that there is at most one job in order to maintain rolling repositories properly.
    2. Handling artifacts and generating new executions when certain nodes are
       satisfied.
    """

    def __init__(
        self, project_slug: str, project_config: "Project", project_directory: str
    ) -> None:
        self._lock = asyncio.Lock()
        """
        A lock synchronizing all accesses to the state.
        """
        self.project_slug = project_slug
        """Project short identifier."""
        self._build: T.Optional["Build"] = None
        """
        Currently running job, if any.  If set, `_job_task` must also be set.
        """
        self._build_task: T.Optional[asyncio.Task[None]] = None
        """
        Task for the currently running job, if any.  If set, `_job` must also be set.
        """
        self._project_config = project_config
        """
        Project configuration this project state is related to.
        """
        self.project_directory: T.Final = project_directory
        """
        Directory in which builds of this project are stored.
        """

    async def run_build(
        self,
        coord: "CoordinatorState",
        increment: bool,
        delay: float,
    ) -> str | None:
        """
        Attempts to run a job if one is not already running.

        Args:
          coord: Coordinator state object.
          increment: If ``True``, starts an incremental build.
          delay: Pre-start delay, in fractional seconds.

        Returns:
          The ID of the new job, or ``None`` if a job is running.
        """
        async with self._lock:
            if self._build is not None:
                return None
            assert self._build_task is None

            # Find latest build at exec time.
            last_build = None
            if increment:
                try:
                    last_build = max(
                        (
                            build_id
                            for build_id in os.listdir(self.project_directory)
                            if xbu_ts.BUILD_ID_RE.match(build_id)
                        ),
                        default=None,
                    )
                    last_build = last_build and path.join(self.project_directory, last_build)
                except FileNotFoundError:
                    # No project directory yet.  Hence, no previous build.
                    pass

            # Generate unique build ID.
            while True:
                build_id = xbu_ts.get_build_id_for_now()
                build_dir = path.join(self.project_directory, build_id)
                try:
                    os.makedirs(build_dir)
                    break
                except FileExistsError:
                    # Hopefully never happens.
                    await asyncio.sleep(1)

            self._build = Build(
                build_id, build_dir, last_build, self.project_slug, self._project_config, delay
            )
            self._build_task = task = coord.add_build_task(self._build.run(coord))
            task.add_done_callback(self._clear_current_build)
            return build_id

    def _clear_current_build(self, task: asyncio.Task[None]) -> None:
        """
        Removes the current job upon completion.
        """
        # XXX: GIL-reliant
        assert self._build is not None
        assert self._build_task is not None
        self._build = None
        self._build_task = None


class Build:
    """
    A build is one attempt to build a project.
    """

    def __init__(
        self,
        build_id: str,
        build_dir: str,
        previous_build_dir: str | None,
        project_slug: str,
        project_config: "Project",
        delay: float,
    ) -> None:
        self.build_id: T.Final = build_id
        """ID of this build.  For logging."""
        self.build_dir: T.Final = build_dir
        """Directory in which this build stores data.  Does not initially exist."""
        self.previous_build_dir: T.Final = previous_build_dir
        self.project_config: T.Final = project_config
        self.start_delay: T.Final = delay
        """
        Start delay, in fractional seconds.  The coordinator should spend this time idle before
        fetching sources to allow authors to merge PRs.
        """
        self.project_slug = project_slug
        self.coordinator_failed = False
        """If true, refuse any further artifacts, logs, et cetera."""
        self._buildsystem: "CoordinatorBuildSystem"
        self._has_more_artifacts: T.Final = asyncio.Event()
        """Set when a build receives an artifact."""

        self._done_artifacts: set[str]
        """Set of artifacts available for use."""
        self._node_states: dict[str, NodeState]
        """Task node status tracking."""
        self._graph: "Graph"
        """Node graph received during setup."""

        self.revision: str
        """Git revision this build is based on."""

        self.log_io: "BuildLogger"
        """File in which one can write user-visible messages from the coordinator."""

    @property
    def log_dir(self) -> str:
        return path.join(self.build_dir, "logs")

    def record_scheduled(self) -> None:
        """
        Record that this build started fetching.
        """
        from .build_state import set_scheduled

        set_scheduled(self.build_dir)

    def record_fetching(self) -> None:
        """
        Record that this build started fetching.
        """
        from .build_state import set_build_fetching

        set_build_fetching(self.build_dir)

    def record_start_setup(self, revision: str) -> None:
        """
        Record that this build moved into the ``SETUP`` phase, out of ``FETCHING``.
        """
        from .build_state import set_build_setup

        self.revision = revision
        set_build_setup(self.build_dir, revision)

    def record_setup_done(self) -> None:
        """
        Record that the build system finished initializing, and is now ``CALCULATING``.
        """
        from .build_state import set_build_calculating

        set_build_calculating(self.build_dir)

    def set_graph(self, graph: "Graph") -> None:
        """
        Record the task graph.  Also sets the state to ``RUNNING``.
        """
        from .build_state import store_graph

        self._node_states = {
            iden: NodeState.UP_TO_DATE if node.is_up_to_date else NodeState.WAITING
            for (iden, node) in graph.nodes.items()
        }
        self._done_artifacts = set(
            ident for (ident, node) in graph.artifacts.items() if node.is_up_to_date
        )
        self._graph = graph

        store_graph(self.build_dir, graph)

    def mark_as_coordinator_fail(self) -> None:
        """
        Record that the coordinator failed.
        """
        from .build_state import set_coordinator_failed

        self.coordinator_failed = True
        set_coordinator_failed(self.build_dir)

    def set_node_state(self, identifier: str, new_state: NodeState) -> None:
        """
        Record that the node identified by ``identifier`` moved into state
        ``new_state``.
        """
        from .build_state import set_node_state

        self._node_states[identifier] = new_state
        set_node_state(self.build_dir, identifier, new_state)

    # Implemented in build_run.py.
    run = run
