# Build system abstraction.
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
This module contains the base classes for all build systems.

A build system is an abstraction that allows xbbs to build up and execute a graph
without understanding the underlying steps.
"""

import enum
import typing as T
from abc import ABC, abstractmethod

from xbbs.utils.logging.build_logger import BuildLogger

from . import dag

if T.TYPE_CHECKING:
    from xbbs.data.config import Project


class CoordinatorBuildSystem(ABC):
    """
    A ``CoordinatorBuildSystem`` encapsulates the steps needed to compute a graph and
    process artifacts on the coordinator.  Steps that go into a build, in order, are:

    #. Initialize coordinator work directory.
    #. Compute the dependency graph into a build-system-agnostic format, taking account
       the previous builds repositories, if present.
    #. Serialize tasks so that they may be handed over to the worker.
    #. Deposit received artifact.

    The coordinator guarantees that one ``CoordinatorBuildSystem`` corresponds to one
    build job, and that one instance is used for the entire job.  As such, the
    ``CoordinatorBuildSystem`` may keep internal state.

    The job directory and project info are passed in via a factory.
    """

    @abstractmethod
    async def prepare_job_directory(self) -> None:
        """
        Performs steps necessary to begin computing the graph in an empty but existing
        job directory.  Should also use ``previous_job_directory`` to populate the new
        directory in case of an incremental build.
        """

    @abstractmethod
    async def compute_job_graph(self) -> dag.Graph:
        """
        Computes the job graph based on the given source tree.  The build system ought
        to also store the graph, as it will be necessary for depositing artifacts.
        """

    @abstractmethod
    async def serialize_task(self, node: dag.Node) -> T.Any:
        """
        Serialize a task for a graph node so that it may be deserialized by
        :py:meth:`BuildSystemFactory.create_worker_build_for_task`.

        Note that the resulting task will be serialized via :py:func:`json.dumps` or
        similar, and so, must be serializable.

        """

    @abstractmethod
    async def deposit_artifact(self, identifier: str, artifact_file: str) -> None:
        """
        Stores an uploaded artifact in the appropriate repository.

        This function ought to be transactional-ish: on failure, it shall remove the
        artifact from its repository.

        Args:
          identifier: An artifact identifier present in the graph returned by
                      :py:meth:`compute_job_graph`.
          artifact_file: Filename of the temporary file containing the uploaded
                         artifact.  The implementation may delete or rename this file,
                         and the caller can unlink the ``artifact_file`` filename.
        """


class ArtifactHelper(ABC):
    """
    Mechanism by which the :py:class:`WorkerBuildSystem` can upload successfully built
    artifacts.
    """

    @abstractmethod
    async def send_artifact(self, artifact_id: str, artifact_location: str) -> None:
        """
        Sends the in location ``artifact_location`` to the coordinator, telling it that
        it is ``artifact_id``.
        """

    @abstractmethod
    async def get_repo_file(self, path: str, local_path: str) -> None:
        """
        Allows downloading a file in location ``path`` in the repositories directory of
        the current build into file ``local_path``.
        """


class WorkerBuildSystem(ABC):
    """
    A ``WorkerBuildSystem`` encapsulates the steps that need to happen on the worker in
    order to process an execution of a task.  These steps, in order, are:

    #. Decode received task.
    #. Execute build, calling back to a :py:class:`ArtifactHelper` for completed
       artifacts, outputting subprocess output into a log ``IO``.

    Note that the artifact helper becomes useless the moment the ``WorkerBuildSystem``
    returns control to the caller, as this indicates job completion.

    All failures during these steps are considered abnormal.  The only way to get a
    normal failure is to return an indication of failure without raising an exception
    in the last step.
    """

    @abstractmethod
    async def execute_task(self, artifact_helper: ArtifactHelper) -> bool:
        """
        Perform steps necessary to execute the task, calling back to the
        ``artifact_helper`` as necessary.

        Returns:
          The status of the build.  If ``True``, the build is considered a success.
        """


class BuildSystemFactory(ABC):
    """
    Produces instances of :py:class:`CoordinatorBuildSystem` and
    :py:class:`WorkerBuildSystem`.
    """

    @abstractmethod
    def create_coordinator_side(
        self,
        log_io: BuildLogger,
        build_id: str,
        src_directory: str,
        job_directory: str,
        repo_directory: str,
        previous_repo_directory: str | None,
        project: "Project",
    ) -> CoordinatorBuildSystem:
        """
        Creates a :py:class:`CoordinatorBuildSystem` for a given project, in a given
        directory.  The constructed instance shall do all its work in the
        ``job_directory``.  It may be given a ``previous_job_directory`` to seed the
        current build, in order to implement incremental updates.

        Args:
          log_io: A logger for user-visible coordinator logs.
          build_id: The build ID assigned to this build.  Useful for logs.  No other
                    semantics.
          src_directory: Location where xbbs clones the ``git`` source tree.  The clone
                         will be fresh.
          job_directory: An empty directory in which the build system ought to work.
                         Deleted after a job is completed.
          repo_directory: An empty directory in which the build system ought to store
                          artifacts.  Will be exposed to workers as-is on an endpoint
                          given to them after receiving a task.
          previous_repo_directory: A completed repo directory of the last job, in case
                                   this build needs to be incrementally built.
                                   Otherwise ``None``.  Note that the previous job might
                                   have failed.  This directory must stay unmodified.
          project: Project configuration that lead to the creation of this job.

        Returns:
          A new :py:class:`CoordinatorBuildSystem` instance for the given ``project``.
        """

    @abstractmethod
    def create_worker_build_for_task(
        self,
        log_io: BuildLogger,
        execution_id: str,
        src_directory: str,
        job_directory: str,
        repo_base_url: str,
        serialized_task: T.Any,
    ) -> WorkerBuildSystem:
        """
        Creates a :py:class:`WorkerBuildSystem` ready to execute the task
        ``coordinator_task``.  Called after initializing the ``src_directory`` to the
        same revision as the coordinator, and preparing an empty ``job_directory``.

        Args:
          log_io: A logger for user-visible worker logs.
          execution_id: The execution ID assigned to this task.  Useful for logs.  No
                        other semantics.
          src_directory: Location where xbbs clones the ``git`` source tree.  The clone
                         will be fresh.
          job_directory: An empty directory in which the build system ought to work.
                         Deleted after a job is completed.
          repo_base_url: Complete URL at which the coordinator exposes the repositories.
                         No trailing slash.
          serialized_task: An arbitrary object as returned by
                           :py:meth:`CoordinatorBuildSystem.serialize_task` from the matching
                           :py:class:`CoordinatorBuildSystem` implementation.
        """


class BuildSystem(enum.Enum):
    """
    Enum of known build system identifiers.  Used for communication and config.
    """

    XBSTRAP = "xbstrap"
    """
    `xbstrap`_ is a meta-meta-build-system for OS distributions used by
    Managarm.

    .. _xbstrap: https://github.com/managarm/xbstrap/
    """


def create_build_system_factory(system: BuildSystem) -> BuildSystemFactory:
    match system:
        case BuildSystem.XBSTRAP:
            from .xbstrap import XbstrapBuildSystemFactory

            return XbstrapBuildSystemFactory()

    # Missed a case?
    T.assert_never()
