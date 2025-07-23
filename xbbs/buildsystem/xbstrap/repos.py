# Repository management.
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
This module contains logic for depositing artifacts into repositories.
"""

import asyncio
import json
import os
import os.path as path
import plistlib
import shutil
import typing as T
from subprocess import CalledProcessError

import xbbs.utils.fs as xbu_fs
import xbbs.utils.proc as xbu_proc
from xbbs.data.config import XbstrapSigningKeyData

from .dag import ArtifactType, XbstrapArtifact


async def _xbps_rindex(arch: str, *args: str, cwd: str | None = None) -> None:
    """
    Runs ``XBPS_ARCH=${arch} xbps-rindex "${args[@]}"``, throwing if it fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "xbps-rindex",
        *args,
        env=xbu_proc.merge_env(dict(XBPS_ARCH=arch)),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    (stdout, _) = await proc.communicate()
    rc = await proc.wait()
    if rc != 0:
        raise CalledProcessError(rc, list("xbps-rindex", *args), output=stdout)


def read_tool_repo_versions(tool_repo: str) -> dict[str, str]:
    """
    Read a presumed-correctly-formatted tool repository ``versions.json`` file.
    """
    try:
        with open(path.join(tool_repo, "versions.json"), "r") as versions:
            return T.cast(dict[str, str], json.load(versions))
    except FileNotFoundError:
        return dict()


def write_tool_repo_version(tool_repo: str, versions: dict[str, str]) -> None:
    """
    Write out ``versions`` as the versions for packages in ``tool_repo``.
    """
    with xbu_fs.atomic_write_open(path.join(tool_repo, "versions.json"), "w") as f:
        json.dump(versions, f)


async def deposit_artifact(
    repo_dir: str,
    artifact: XbstrapArtifact,
    artifact_file: str,
    all_arches: set[str],
    signing_key: XbstrapSigningKeyData | None,
) -> None:
    """
    Write the ``artifact`` with contents ``artifact_file`` into the artifact repository
    ``repo_dir``.
    This function generates a directory structure under ``repo_dir``.  The artifact will
    be stored under ``${repo_dir}/${artifact_type}/${architecture}/${filename}``.

    For :py:data:`ArtifactType.FILE` artifacts, the ``filename`` is the name of the
    artifact.

    For :py:data:`ArtifactType.PACKAGE` artifacts, the ``filename`` is
    ``{name}-{version}.{architecture}.xbps``, and the artifact is registered using
    ``xbps-rindex``.  If the artifact is ``noarch``, it will also be copied into each
    architecture (hence the ``all_arches`` argument).

    For :py:data:`ArtifactType.TOOL` artifacts, the ``filename`` is is
    ``{name}.tar.gz``, and the artifact is registered into a tool registry file, which
    saves a mapping from each tool to the version its archive is at.

    Note that this function provides no concurrency guarantees.

    Args:
      repo_dir: Repository root location.
      artifact: Artifact which we're depositing.
      artifact_file: Path to the temporary file containing the artifact.  Can be moved
                     from.
      all_arches: Set of all architectures present in the project.  Does not include
                  ``noarch``.
      signing_key: Signing key to use to sign package files, if any.
    """
    assert "noarch" not in all_arches

    # Fix the temporary file permissions being over-restrictive.
    os.chmod(artifact_file, 0o644)

    # TODO(arsen): This function will not do cleanup on partial success
    match artifact.data.artifact_type:
        case ArtifactType.PACKAGE:
            arch = artifact.data.architecture
            version = artifact.data.version
            name = artifact.data.name
            package_file = path.join(repo_dir, artifact.repo_file_path)
            package_repo = path.dirname(package_file)
            package_filename = path.basename(package_file)
            os.makedirs(package_repo, exist_ok=True)
            shutil.move(artifact_file, package_file)

            async def _add_and_sign(arch: str, filename: str) -> None:
                """Sign this package file, if signing is enabled"""
                assert arch != "noarch"  # There are no noarch repos.

                # Add the package.
                arch_repo = path.join(repo_dir, "packages", arch)
                await _xbps_rindex(arch, "-fa", package_filename, cwd=arch_repo)

                if not signing_key:
                    return

                # Figure out the name of the signer.
                with open(signing_key.public_key, "rb") as pkey:
                    signature_by = plistlib.load(pkey)["signature-by"]

                # Initialize the repository metadata.
                privkey = path.abspath(signing_key.private_key)
                await _xbps_rindex(
                    arch, "--signedby", signature_by, "--privkey", privkey, "-s", arch_repo
                )
                # Sign the actual artifact.
                await _xbps_rindex(
                    arch,
                    "--signedby",
                    signature_by,
                    "--privkey",
                    privkey,
                    "-S",
                    filename,
                    cwd=arch_repo,
                )
                # Remove obsoletes.
                await _xbps_rindex(arch, "-r", arch_repo, cwd=arch_repo)

            if arch == "noarch":
                # Duplicate into all repos, not the noarch one, as those are what
                # xbps-install will be reading.
                # TODO(arsen): remove obsoletes from noarch, or switch to (ab)using reflinks for
                # deduplication
                for arch in all_arches:
                    arch_repo = path.join(repo_dir, "packages", arch)
                    os.makedirs(arch_repo, exist_ok=True)
                    os.symlink(
                        path.join("../noarch", package_filename),
                        path.join(arch_repo, package_filename),
                    )
                    await _add_and_sign(arch, package_filename)
            else:
                await _add_and_sign(arch, package_file)
            return

        case ArtifactType.TOOL:
            arch = artifact.data.architecture
            version = artifact.data.version
            name = artifact.data.name
            tool_in_repo = path.join(repo_dir, artifact.repo_file_path)
            tool_repo = path.dirname(tool_in_repo)
            tool_filename = path.basename(tool_in_repo)
            os.makedirs(tool_repo, exist_ok=True)
            shutil.move(artifact_file, tool_in_repo)

            # Update version info.
            versions = read_tool_repo_versions(tool_repo)
            versions[name] = version
            write_tool_repo_version(tool_repo, versions)

            if arch == "noarch":
                # Also link into all repos.  We intentionally leave their tool versions unaffected
                # because they will be merged together at incremental build load time anyway.
                for arch in all_arches:
                    arch_repo = path.join(repo_dir, "tools", arch)
                    os.makedirs(arch_repo, exist_ok=True)
                    os.symlink(
                        path.join("../noarch", tool_filename),
                        path.join(arch_repo, tool_filename),
                    )
            return

        case ArtifactType.FILE:
            file = path.join(repo_dir, "files", artifact.data.architecture, artifact.data.name)
            file_repo = path.dirname(file)
            os.makedirs(file_repo, exist_ok=True)
            shutil.move(artifact_file, file)
            return

    # All cases covered.
    T.assert_never()
