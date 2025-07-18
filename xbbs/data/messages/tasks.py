# Task-related messages.
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
This module contains messages related to exchanging a task between a worker and the
coordinator.
"""

import typing as T

from pydantic import BaseModel

from xbbs.buildsystem import BuildSystem


class TaskRequest(BaseModel):
    """
    This message tells the coordinator what the capabilities of the worker are, and that
    it is ready to receive another job.

    The worker must not send this message while it is responsible for executing a job.
    """

    msg_type: T.Literal["BORED"]
    capabilities: set[str]
    """Capabilities this worker possesses."""


class TaskResponse(BaseModel):
    """
    A task ready for execution by some worker.  Contains the build-system specific task
    information to be decoded.
    """

    msg_type: T.Literal["WORK!"]
    execution_id: str
    """Unique per execution identifier."""

    git: str
    """Git source of the repository being built."""

    revision: str
    """Git hash we're building off of."""

    repo_url_path: str
    """Path component where the build repository directory is exposed."""

    buildsystem: BuildSystem
    """Build system to use for the build."""

    buildsystem_specific: T.Any
    """
    Build system-specific information, as obtained by
    :py:meth:`xbbs.buildsystem.CoordinatorBuildSystem.serialize_task`.
    """
    # TODO(arsen): mirror URL


class CancelTask(BaseModel):
    """
    Lets the coordinator tell the worker to give up on a certain execution.
    """

    msg_type: T.Literal["STOP"]
    execution_id: str
    """
    Which execution to give up on?  If this execution is not running, the message is silently
    ignored.
    """


class TaskDone(BaseModel):
    """Indicates that an execution completed in some manner."""

    msg_type: T.Literal["DONE!"]
    execution_id: str
    """ID of the completed execution."""

    run_time: float
    """How long did it take?  In fractional seconds."""

    status: T.Literal["S", "F", "A"]
    """
    Was it successful?  If the value is ``S``, the build is considered a success.  If the value is
    ``F``, the build failed normally, and if the value is ``A``, the build failed abnormally and
    should be re-scheduled.
    """


class LogMessage(BaseModel):
    """
    Encapsulates a log line sent from the worker.
    """

    msg_type: T.Literal["L"]

    execution_id: str
    """Execution this log line is part of."""

    log_line: bytes
    """Log contents."""
