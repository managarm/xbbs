# Coordinator status.
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
This module contains a model describing the status of the coordinator and workers.
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel


@dataclass
class CurrentExecution:
    """
    Brief class containing info about what the worker is currently up to.
    """

    project_slug: str
    """Slug of the project being built at the moment."""
    build_id: str
    """Build ID within that project being built at the moment."""
    node_id: str
    """Job graph node being built at this moment."""
    execution_id: str
    """ID of the currently-active execution that builds the aforementioned node."""


class WorkerStatus(BaseModel):
    """
    Basic information on the status of a single worker.
    """

    id: int
    """
    Worker ID.  A number guaranteed to be unique even when workers restart that carries
    no other significance.
    """

    hostname: str
    """Worker hostname."""

    load_avg: tuple[float, float, float]
    """Worker load average."""

    last_seen: datetime
    """TZ-aware timestamp of when the worker was last seen."""

    # TODO(arsen): show current job
    current_execution: CurrentExecution | None
    """If present, the active execution.  Otherwise, the worker is resting."""


class Project(BaseModel):
    """
    Summary of a project.  Dual of :py:class:`xbbs.coordinator.project.ProjectState`.
    """

    project_name: str
    """Display name of the project.  ``name`` field of the project configuration."""

    description: str
    """
    Brief project description.  ``description`` field of the project configuration.
    """

    classes: list[str]
    """CSS classes to add to this projects listing."""

    running: bool
    """``True`` iff there is a build of this project currently running."""


class CoordinatorStatus(BaseModel):
    """
    Basic information on the status of the coordinator.
    """

    hostname: str
    """Coordinator hostname."""

    load_avg: tuple[float, float, float]
    """Coordinator load average."""

    workers: list[WorkerStatus]
    """Status of each currently connected worker."""

    projects: dict[str, Project]
    """Summary of the registered projects."""
