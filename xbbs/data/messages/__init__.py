# Common helpers for all messages.
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
This module contains functions used for serializing and deserializing messages.
"""

import typing as T

import msgpack
import pydantic

from . import heartbeat, tasks

AnyMessage: T.TypeAlias = T.Union[
    # Heartbeats.
    heartbeat.WorkerHeartbeat,
    # Task messages.
    tasks.TaskRequest,
    tasks.TaskResponse,
    tasks.LogMessage,
    tasks.TaskDone,
    tasks.CancelTask,
]
"""
A message of yet-undetermined type.
"""


_AnnotatedAnyMessage: T.TypeAlias = T.Annotated[
    AnyMessage, pydantic.Field(discriminator="msg_type")
]
_ANY_MESSAGE_ADAPTER = pydantic.TypeAdapter[_AnnotatedAnyMessage](_AnnotatedAnyMessage)


def serialize(message: AnyMessage) -> bytes:
    """
    Encapsulate a message into a MesagePack string.
    """
    return msgpack.packb(_ANY_MESSAGE_ADAPTER.dump_python(message, mode="json"))


def deserialize(message: bytes) -> AnyMessage:
    """
    Decode a message previously saved using :py:func:`serialize`.
    """
    return _ANY_MESSAGE_ADAPTER.validate_python(msgpack.unpackb(message))
