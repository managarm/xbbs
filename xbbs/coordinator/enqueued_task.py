# A task for the task queue.
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


from dataclasses import dataclass

from xbbs.data.messages.tasks import TaskResponse


@dataclass
class EnqueuedTask:
    """Elements of the outgoing task queue."""

    task: TaskResponse
    capabilities: set[str]

    build_dir: str
    """
    Build directory that owns this execution.  Used to update its status to ``RUNNING``.
    """
    project_slug: str
    """
    Short name of the project requesting this task.
    """
    build_id: str
    """
    Build ID that owns this execution.
    """
    node_id: str
    """
    Identifier of the node requesting this task.
    """
