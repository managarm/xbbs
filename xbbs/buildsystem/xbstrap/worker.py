# Worker part of the xbstrap<->xbbs interface.
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
This module contains the worker build system implementation based on xbstrap.
"""

import asyncio
import json
import logging
import os
import os.path as path
import shutil
import sys
import tarfile
import typing as T

import yaml

import xbbs.utils.fs as xbu_fs
import xbbs.utils.pipe as xbu_pipe
import xbbs.utils.proc as xbu_proc
from xbbs.utils.logging.build_logger import BuildLogger

from .. import ArtifactHelper, WorkerBuildSystem
from .common import XbstrapTask
from .dag import ArtifactType, artifact_identifier

logger = logging.getLogger(__name__)


async def _parse_notification_stream(stream: asyncio.StreamReader) -> T.AsyncGenerator[T.Any]:
    buffer = ""
    async for line_ in stream:
        line = line_.decode()
        buffer += line
        if line.strip() == "...":
            document = yaml.safe_load(buffer)
            buffer = ""

            yield document


class XbstrapWorkerBuildSystem(WorkerBuildSystem):
    """
    Worker-side xbbs build system implementation for xbstrap.
    """

    def __init__(
        self,
        log_io: BuildLogger,
        execution_id: str,
        src_directory: str,
        job_directory: str,
        repo_base_url: str,
        serialized_task: T.Any,
    ) -> None:
        self.log_io = log_io
        self.execution_id = execution_id
        self.src_directory = src_directory
        self.job_directory = job_directory
        self.repo_base_url = repo_base_url
        self.task = XbstrapTask.model_validate(serialized_task)
        self.log = logging.LoggerAdapter(logger, dict(execution_id=execution_id))

    async def _cmd(
        self, *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> None:
        """Run a command in the job directory and wait for it to finish."""
        await xbu_proc.do_command(self.log_io, *args, cwd=cwd or self.job_directory, env=env)

    async def _call(self, *args: str) -> bytes:
        """Run a command in the job directory and get its output."""
        return await xbu_proc.get_command_output(self.log_io, *args, cwd=self.job_directory)

    async def execute_task(self, artifact_helper: ArtifactHelper) -> bool:
        # Install the distfiles.
        distfile_path = self.task.distfile_path
        if distfile_path:
            xbu_fs.merge_tree_into(
                path.join(self.src_directory, distfile_path), self.job_directory
            )

        # Fun directories.
        sysroot = path.join(self.job_directory, "system-root")
        keysdir = path.join(sysroot, "var/db/xbps/keys")
        os.makedirs(keysdir)
        tools_dir = path.join(self.job_directory, "tools")
        pkgs_dir = path.join(self.job_directory, "xbps-repo")
        os.makedirs(tools_dir)

        await self._cmd("xbstrap", "init", self.src_directory)
        with open(path.join(self.src_directory, "bootstrap-commits.yml"), "w") as comm:
            commit_obj: dict[str, dict[str, T.Any]] = {  # should make a typed dict
                "general": {},
                "commits": self.task.commits_object,
            }
            if self.task.mirror_root:
                commit_obj["general"]["xbstrap_mirror"] = self.task.mirror_root
            json.dump(commit_obj, comm)

        for fingerprint, key in self.task.xbps_keys.items():
            with open(path.join(keysdir, f"{fingerprint}.plist"), "wb") as pubkey:
                pubkey.write(key)

        arches = set(
            self.task.referenced_artifacts[dependency].data.architecture
            for dependency in self.task.task.dependencies
            if self.task.referenced_artifacts[dependency].data.architecture != "noarch"
        )

        if len(arches) >= 2:
            self.log.error("cannot currently build multiarch builds (got %r)", arches)
            return False
        if arches:
            # There's something to install.
            (arch,) = arches
            await self._cmd(
                "xbps-install",
                "-Uy",
                "-R",
                self.repo_base_url + f"/packages/{arch}/",
                "-r",
                sysroot,
                "-SM",
                "--",
                *list(
                    dependency.data.name
                    for dependency in self.task.get_dependencies()
                    if dependency.data.artifact_type == ArtifactType.PACKAGE
                ),
                env=dict(XBPS_ARCH=arch),
            )

        # We also need to populate a local repository directory.  xbps-install already
        # downloaded all the packages we need, we'll just copy them into the xbps
        # repository.
        cache_dir = path.join(sysroot, "var/cache/xbps")
        os.makedirs(pkgs_dir, exist_ok=True)
        for dependency in self.task.get_dependencies():
            if dependency.data.artifact_type != ArtifactType.PACKAGE:
                continue
            pkg_filename = path.basename(dependency.repo_file_path)
            shutil.copy2(path.join(cache_dir, pkg_filename), path.join(pkgs_dir, pkg_filename))
            await self._cmd(
                "xbps-rindex", "-fa", "--", pkg_filename, cwd=pkgs_dir, env=dict(XBPS_ARCH=arch)
            )

        # Install tools
        for artifact in self.task.get_dependencies():
            if artifact.data.artifact_type != ArtifactType.TOOL:
                continue
            this_tool_dir = path.join(tools_dir, artifact.data.name)
            os.makedirs(this_tool_dir, exist_ok=True)
            tool_local_file = path.join(tools_dir, f"{artifact.data.name}.tar.gz")
            await artifact_helper.get_repo_file(artifact.repo_file_path, tool_local_file)
            with tarfile.open(tool_local_file, "r") as tar:
                if sys.version_info >= (3, 12):
                    # Maybe should have a more aggressive filter, but we have
                    # legitimate "evil-looking" tool tars (specifically, GCC
                    # tarballs link to the binutils directory, which is outside
                    # of the root)
                    tar.extractall(path=this_tool_dir, filter="fully_trusted")
                else:
                    tar.extractall(path=this_tool_dir)

        (notification_pipe_r, notification_pipe_w) = os.pipe()
        async with xbu_pipe.pipe_text_reader(notification_pipe_r) as notification_reader:
            del notification_pipe_r
            try:
                runner_proc = await asyncio.create_subprocess_exec(
                    "xbstrap-pipeline",
                    "run-job",
                    "--keep-going",
                    "--progress-file",
                    f"fd:{notification_pipe_w}",
                    self.task.task.identifier,
                    cwd=self.job_directory,
                    pass_fds=(notification_pipe_w,),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=self.log_io.out_stream,
                    stderr=self.log_io.out_stream,
                )
            finally:
                # Make sure this is always closed after attempting to start the proc
                os.close(notification_pipe_w)
            del notification_pipe_w

            async for notification in _parse_notification_stream(notification_reader):
                logger.debug("got notify %r", notification)
                assert isinstance(notification, dict)

                if notification["status"] != "success":
                    # Something failed.  Will be recorded as such when this execution is
                    # completed.
                    continue

                action = notification["action"]
                subject = notification["subject"]
                artifact_files = notification["artifact_files"]
                # TODO(arsen): ignore error?
                if action == "archive-tool":
                    arch = notification["architecture"]
                    fn = path.join(tools_dir, f"{subject}.tar.gz")
                    await artifact_helper.send_artifact(
                        artifact_identifier(ArtifactType.TOOL, arch, subject), fn
                    )
                elif action == "pack":
                    arch = notification["architecture"]
                    identifier = artifact_identifier(ArtifactType.PACKAGE, arch, subject)
                    artifact = self.task.referenced_artifacts[identifier]
                    await artifact_helper.send_artifact(
                        identifier, path.join(pkgs_dir, path.basename(artifact.repo_file_path))
                    )
                elif len(artifact_files) == 0:
                    continue
                for file in artifact_files:
                    # ArtifactType.FILE case
                    name = file["name"]
                    arch = file["architecture"]
                    filepath = file["filepath"]
                    identifier = artifact_identifier(ArtifactType.FILE, arch, name)
                    artifact = self.task.referenced_artifacts[identifier]
                    await artifact_helper.send_artifact(
                        identifier, path.join(self.job_directory, filepath)
                    )

        # Parsed entire notification stream.  Wait for xbstrap to finish.
        return await runner_proc.wait() == 0
