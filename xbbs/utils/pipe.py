# Helpers for working with pipes.
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
This module contains utility functions for working with pipes.
"""

import asyncio
import contextlib
import typing as T


@contextlib.asynccontextmanager
async def pipe_text_reader(fd: int | T.TextIO) -> T.AsyncGenerator[asyncio.StreamReader, None]:
    """Get a :py:class:`asyncio.StreamReader` for a pipe."""
    if isinstance(fd, int):
        fd = open(fd, "rt")
    with fd:
        transport = None
        reader = asyncio.StreamReader()
        try:
            transport, _ = await asyncio.get_event_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), fd
            )
            yield reader
        finally:
            if transport:
                transport.close()
