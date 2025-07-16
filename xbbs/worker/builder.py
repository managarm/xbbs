# Build logic.
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
This module contains the worker build steps.
"""

import os
import os.path as path
import typing as T

import xbbs.data.messages as xbd_messages
import xbbs.utils.proc as xbu_proc
from xbbs.buildsystem import ArtifactHelper, create_build_system_factory

if T.TYPE_CHECKING:
    from xbbs.utils.logging.build_logger import BuildLogger


async def build_task(
    work_dir: str,
    artifact_uploader: ArtifactHelper,
    task: xbd_messages.tasks.TaskResponse,
    log_io: "BuildLogger",
    repo_base_url: str,
) -> bool:
    """
    Execute the steps necessary to build ``task``, redirecting output to ``log_io``.
    """
    src_dir = path.join(work_dir, "src")
    build_dir = path.join(work_dir, "build")
    for dir in (src_dir, build_dir):
        os.makedirs(dir)

    async def _cmd(cwd: str, *args: str) -> None:
        await xbu_proc.do_command(log_io, *args, cwd=cwd)

    # Clone sources.
    await _cmd(src_dir, "git", "clone", task.git, ".")
    await _cmd(src_dir, "git", "checkout", task.revision)

    builder = create_build_system_factory(task.buildsystem).create_worker_build_for_task(
        log_io=log_io,
        execution_id=task.execution_id,
        src_directory=src_dir,
        job_directory=build_dir,
        repo_base_url=repo_base_url,
        serialized_task=task.buildsystem_specific,
    )

    result = await builder.execute_task(artifact_uploader)
    log_io.info(f"Build {'succeeded' if result else 'failed'}.")
    return result
