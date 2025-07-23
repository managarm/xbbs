# Coordinator part of the xbstrap<->xbbs interface.
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
This module contains the coordinator build system implementation based on xbstrap.
"""

import asyncio
import itertools
import json
import os.path as path
import shutil
import typing as T

import xbbs.utils.fs as xbu_fs
import xbbs.utils.proc as xbu_proc
from xbbs.data.config import Project, XbstrapProjectConfig

from .. import CoordinatorBuildSystem, dag
from .common import CommitObject, XbstrapTask
from .dag import (
    XbstrapNode,
    parse_raw_dag_into_xbbs_dag,
)
from .raw_dag import parse_raw_dag
from .repos import deposit_artifact, read_tool_repo_versions
from .xbps import read_xbps_repodata

if T.TYPE_CHECKING:
    from xbbs.utils.logging.build_logger import BuildLogger


# Disgusting hack.  Remove later, when Debian gets Python 3.14
copytree_cfr = shutil.copytree
if not hasattr(shutil, "_USE_CP_COPY_FILE_RANGE"):
    import xbbs.utils.backports.shutil_314 as shutil_314

    copytree_cfr = shutil_314.copytree


class XbstrapCoordinatorBuildSystem(CoordinatorBuildSystem):
    """
    Coordinator-side xbbs build system implementation for xbstrap.
    """

    def __init__(
        self,
        log_io: "BuildLogger",
        build_id: str,
        src_directory: str,
        job_directory: str,
        repo_directory: str,
        previous_repo_directory: str | None,
        project: Project,
    ) -> None:
        self.log_io = log_io
        self.build_id = build_id
        self.src_directory: T.Final = src_directory
        self.job_directory: T.Final = job_directory
        self.repo_directory: T.Final = repo_directory
        self.previous_repo_directory: T.Final = previous_repo_directory
        self.project: T.Final = project
        assert isinstance(project.buildsystem, XbstrapProjectConfig)
        self.commits_object: CommitObject | None

        self._artifact_deposit_lock = asyncio.Lock()
        """Synchronize article depositing."""

        # TODO(arsen): should validate the keypair somewhere.

    async def _cmd(self, *args: str) -> None:
        """Run a command in the job directory and wait for it to finish."""
        await xbu_proc.do_command(self.log_io, *args, cwd=self.job_directory)

    async def _call(self, *args: str, input: bytes | None = None) -> bytes:
        """Run a command in the job directory and get its output."""
        return await xbu_proc.get_command_output(
            self.log_io, *args, cwd=self.job_directory, input=input
        )

    def _initialize_repositories(self) -> None:
        """Initialize our build repositories based on a previous build."""
        assert self.previous_repo_directory
        # We only care about these two types.
        for artifact_type in ("packages", "tools"):
            try:
                source = path.join(self.previous_repo_directory, artifact_type)
                copytree_cfr(
                    source,
                    path.join(self.repo_directory, artifact_type),
                    # The package repositories contain links to the noarch repositories.
                    # No point duping those.
                    symlinks=True,
                )
            except FileNotFoundError as e:
                if e.filename != source:
                    # We don't care if the source doesn't exist.
                    raise

    async def prepare_job_directory(self) -> None:
        if self.previous_repo_directory:
            await asyncio.to_thread(self._initialize_repositories)

        # Install the distfiles.
        distfile_path = self.project.buildsystem.distfile_path
        if distfile_path:
            xbu_fs.merge_tree_into(
                path.join(self.src_directory, distfile_path), self.job_directory
            )

        # TODO(arsen): Move into a new stage so that we can have a special status for it?
        mirror_cfg = self.project.buildsystem.mirror
        if mirror_cfg:
            self.log_io.info("Mirros configured.  Updating.")
            await self._cmd(
                "xbstrap-mirror",
                "-C",
                mirror_cfg.build_directory,
                "-S",
                self.src_directory,
                "update",
                "--keep-going",
            )

        # Prepare build tree.
        await self._cmd("xbstrap", "init", self.src_directory)
        # Fetch repositories with rolling versions.
        await self._cmd("xbstrap", "rolling-versions", "fetch")
        # Fetch repositories with variable commits.
        await self._cmd("xbstrap", "variable-commits", "fetch", "--check")

    def _load_versions(self) -> dict[str, T.Any]:
        """Format a ``--version-file``-shaped object."""
        # TODO(arsen): xbstrap assumes one arch here..
        tool_repos = path.join(self.repo_directory, "tools")
        pkg_repos = path.join(self.repo_directory, "packages")
        arches = set().union(
            xbu_fs.listdir_or_empty(tool_repos), xbu_fs.listdir_or_empty(pkg_repos)
        )
        arches.discard("noarch")
        if len(arches) > 1:
            raise RuntimeError("multiarch build not supported by xbstrap yet")
        if len(arches) == 0:
            return dict[str, T.Any](tools={}, pkgs={})

        (arch,) = arches

        tools = read_tool_repo_versions(path.join(tool_repos, arch))
        tools.update(read_tool_repo_versions(path.join(tool_repos, "noarch")))
        try:
            packages = {
                package: info["pkgver"].rpartition("-")[2]
                for (package, info) in read_xbps_repodata(
                    path.join(pkg_repos, arch, f"{arch}-repodata")
                ).items()
            }
        except FileNotFoundError:
            packages = dict()

        return dict(tools=tools, pkgs=packages)

    async def compute_job_graph(self) -> dag.Graph:
        _call = self._call

        # TODO(arsen): make these stream the data
        # Collect the rolling versions and pinned commits.
        rolling_ids: dict[str, str] = json.loads(
            await _call("xbstrap", "rolling-versions", "determine", "--json")
        )
        variable_commits: dict[str, str] = json.loads(
            await _call("xbstrap", "variable-commits", "determine", "--json")
        )

        # Format the commit objects.  XXX(arsen): these commit object dictionaries can
        # be missing one or both of rolling_id and fixed_commit.  Is that intended?  I
        # am not sure when one should be present but not the other.
        commits_object: CommitObject = {
            pkg: dict(
                **(dict(rolling_id=rolling_ids[pkg]) if pkg in rolling_ids else {}),
                **(dict(fixed_commit=variable_commits[pkg]) if pkg in variable_commits else {}),
            )
            for pkg in set[str](itertools.chain(rolling_ids, variable_commits))
        }
        self.commits_object = commits_object

        # Write it to disk.
        def _write_comm_object() -> None:
            commits_file = path.join(self.src_directory, "bootstrap-commits.yml")
            with open(commits_file, "w") as rf:
                json.dump({"commits": commits_object}, rf)

        await asyncio.to_thread(_write_comm_object)
        del _write_comm_object

        # Get the version info for incremental builds.
        versions = dict[str, T.Any](tools={}, pkgs={})
        if self.previous_repo_directory:
            versions = self._load_versions()

        # Build the DAG.
        graph = parse_raw_dag(
            await _call(
                "xbstrap-pipeline",
                "compute-graph",
                "--artifacts",
                "--json",
                "--version-file",
                "fd:0",
                input=json.dumps(versions).encode(),
            )
        )

        self._graph = parse_raw_dag_into_xbbs_dag(graph)
        self._all_arches = set(
            a.data.architecture
            for a in self._graph.artifacts.values()
            if a.data.architecture != "noarch"
        )

        if len(self._all_arches) > 1:
            self.log_io.error(
                f"Multi-architecture builds are not supported yet (got {self._all_arches!r})"
            )
            raise RuntimeError("Multi-arch builds not supported yet")

        return self._graph

    async def serialize_task(self, node: dag.Node) -> T.Any:
        assert isinstance(node, XbstrapNode) and self.commits_object

        # Load the public key used for this project, if any.
        keys_dict = dict[str, bytes]()
        key = self.project.buildsystem.signing_key
        if key:
            with open(key.public_key, "rb") as pubkey_f:
                keys_dict[key.fingerprint] = pubkey_f.read()

        task = XbstrapTask(
            referenced_artifacts={
                k: self._graph.artifacts[k]
                for k in itertools.chain(node.dependencies, node.products)
            },
            task=node,
            commits_object=self.commits_object,
            xbps_keys=keys_dict,
            distfile_path=self.project.buildsystem.distfile_path,
            mirror_root=(
                self.project.buildsystem.mirror.base_url
                if self.project.buildsystem.mirror
                else None
            ),
        )
        return task.model_dump(mode="json")

    async def deposit_artifact(self, identifier: str, artifact_file: str) -> None:
        artifact = self._graph.artifacts[identifier]
        async with self._artifact_deposit_lock:
            await deposit_artifact(
                self.repo_directory,
                artifact,
                artifact_file,
                self._all_arches,
                self.project.buildsystem.signing_key,
            )
