# Utilities for dealing with processes.
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
This module contains utilities for executing subprocesses.
"""

import asyncio
import os
import shlex
import typing as T
from subprocess import CalledProcessError

if T.TYPE_CHECKING:
    from .fs import AnyPath
    from .logging.build_logger import BuildLogger


async def do_command(
    log_stream: "BuildLogger",
    *args: str,
    env: dict[str, str] | None = None,
    cwd: T.Optional["AnyPath"] = None,
    input: bytes | None = None,
) -> None:
    """
    Runs a subprocess, discarding its output, and throwing an exception if it fails.
    """
    log_stream.info(f"Running command {shlex.join(args)} (env={env!r}, cwd={cwd!r})")
    if env:
        environ = os.environ.copy()
        environ.update(env)
    else:
        environ = None
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL if input is None else asyncio.subprocess.PIPE,
        env=environ,
        cwd=cwd,
        stdout=log_stream.out_stream,
        stderr=asyncio.subprocess.STDOUT,
    )
    if input is not None:
        await proc.communicate(input)
    rc = await proc.wait()
    log_stream.info(
        f"Exit code: {rc}",
    )
    if rc != 0:
        raise CalledProcessError(rc, args)


def merge_env(env: dict[str, str]) -> dict[str, str]:
    """Gets the current :py:data:`os.environ`, modified with ``env``."""
    environ = os.environ.copy()
    environ.update(env)
    return environ


async def get_command_output(
    log_stream: "BuildLogger",
    *args: str,
    env: dict[str, str] | None = None,
    cwd: T.Optional["AnyPath"] = None,
    input: bytes | None = None,
) -> bytes:
    """
    Runs a subprocess, collecting its output, and throwing an exception if it fails.
    """
    log_stream.info(f"Capturing command {shlex.join(args)} (env={env!r}, cwd={cwd!r})")
    environ = env and merge_env(env)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL if input is None else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=log_stream.out_stream,
        env=environ,
        cwd=cwd,
    )
    (stdout, _) = await proc.communicate(input=input)
    rc = proc.returncode
    assert rc is not None
    log_stream.info(f"Exit code: {rc}")
    if rc != 0:
        raise CalledProcessError(rc, args)
    return stdout
