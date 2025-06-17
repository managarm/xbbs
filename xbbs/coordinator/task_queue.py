# FIFO outgoing task queue.
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
This module contains the outgoing task queue.
"""

import asyncio
import typing as T
from collections.abc import Container


class CapabilityHolder(T.Protocol):
    """A protocol for anything that contains capabilities"""

    capabilities: set[str]


class TaskQueue[T: CapabilityHolder]:
    """
    A queue of tasks with capabilities attached to them.  Allows dequeue-ing a task
    based on capabilities of the caller, in a mostly fair order.
    """

    # TODO(arsen): make faster

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue = list[T]()
        self._new_task = asyncio.Condition(self._lock)
        """Notified (all) when a task is added."""

    async def enqueue(self, task: T) -> None:
        """
        Adds a task to the queue, waking up a worker if it is capable of processing
        it.
        """
        async with self._lock:
            self._queue.append(task)
            self._new_task.notify_all()

    async def dequeue(self, available_caps: Container[str]) -> T:
        """Get a task which can be executed on this worker."""
        async with self._lock:
            while True:
                for i in range(len(self._queue)):
                    if all(c in available_caps for c in self._queue[i].capabilities):
                        return self._queue.pop(i)
                await self._new_task.wait()


# TODO(arsen): test
