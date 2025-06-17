# xbstrap<->xbbs interface.
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
Root module for the `xbstrap`_-based build system implementation.

The implementation presumes xbstrap_ will be used in combination with `xbps`_.

.. _`xbstrap`: https://github.com/managarm/xbstrap
.. _`xbps`: https://github.com/void-linux/xbps/
"""

import typing as T

from xbbs.data.config import Project
from xbbs.utils.logging.build_logger import BuildLogger

from .. import BuildSystemFactory
from .coordinator import XbstrapCoordinatorBuildSystem
from .worker import XbstrapWorkerBuildSystem


class XbstrapBuildSystemFactory(BuildSystemFactory):
    """Factory for build systems based on ``xbstrap``."""

    def create_coordinator_side(
        self,
        log_io: BuildLogger,
        build_id: str,
        src_directory: str,
        job_directory: str,
        repo_directory: str,
        previous_repo_directory: str | None,
        project: Project,
    ) -> XbstrapCoordinatorBuildSystem:
        return XbstrapCoordinatorBuildSystem(
            log_io,
            build_id,
            src_directory,
            job_directory,
            repo_directory,
            previous_repo_directory,
            project,
        )

    def create_worker_build_for_task(
        self,
        log_io: BuildLogger,
        execution_id: str,
        src_directory: str,
        job_directory: str,
        repo_base_url: str,
        serialized_task: T.Any,
    ) -> XbstrapWorkerBuildSystem:
        return XbstrapWorkerBuildSystem(
            log_io,
            execution_id,
            src_directory,
            job_directory,
            repo_base_url,
            serialized_task,
        )


if T.TYPE_CHECKING:
    # Ensure the class is fully implemented.
    XbstrapBuildSystemFactory()
