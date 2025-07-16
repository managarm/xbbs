# Build-system specific DAG implementation for xbstrap.
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
This module contains the ``xbstrap`` implementation of the ``BuildSystem`` DAG.

See :py:mod:`xbbs.buildsystem.dag`.
"""

import enum
import itertools
import os.path as path
import typing as T

from pydantic import BaseModel, Field

from .raw_dag import (
    PipelineJob,
    PipelineJobNeeds,
    PipelineJobProducts,
)


class ArtifactType(enum.StrEnum):
    TOOL = "tool"
    PACKAGE = "package"
    FILE = "file"


def artifact_identifier(atype: ArtifactType, arch: str, name: str) -> str:
    return f"{atype.value}-{arch}:{name}"


class XbstrapArtifactFileData(BaseModel):
    artifact_type: T.Literal[ArtifactType.FILE]
    name: str
    architecture: str

    def compute_repo_file_path(self) -> str:
        return path.join("files", self.architecture, self.name)


class XbstrapArtifactData(BaseModel):
    artifact_type: T.Literal[ArtifactType.PACKAGE, ArtifactType.TOOL]
    name: str
    version: str
    architecture: str

    def compute_repo_file_path(self) -> str:
        match self.artifact_type:
            case ArtifactType.PACKAGE:
                return path.join(
                    "packages",
                    self.architecture,
                    f"{self.name}-{self.version}.{self.architecture}.xbps",
                )
            case ArtifactType.TOOL:
                return path.join(
                    "tools", self.architecture, f"{self.name}.tar.gz"  # XXX(arsen): assumes gzip
                )

        # Should cover all cases.
        T.assert_never()


class XbstrapArtifact(BaseModel):
    """
    A :py:class:`Artifact` implementation for ``xbstrap``.
    """

    is_up_to_date: bool

    data: XbstrapArtifactData | XbstrapArtifactFileData = Field(discriminator="artifact_type")

    @property
    def identifier(self) -> str:
        return artifact_identifier(self.data.artifact_type, self.data.architecture, self.data.name)

    @property
    def repo_file_path(self) -> str:
        return self.data.compute_repo_file_path()


class XbstrapNode(BaseModel):
    """
    A :py:class:`Node` implementation for ``xbstrap``.
    """

    identifier: str
    dependencies: frozenset[str]
    products: frozenset[str]
    is_up_to_date: bool
    is_unstable: bool
    required_capabilities: frozenset[str]


class XbstrapGraph(BaseModel):
    """
    A :py:class:`Graph` implementation for ``xbstrap``.
    """

    nodes: dict[str, XbstrapNode]
    artifacts: dict[str, XbstrapArtifact]


def parse_raw_dag_into_xbbs_dag(raw_dag: dict[str, PipelineJob]) -> XbstrapGraph:
    """Convert ``xbstrap-pipeline`` result into an XBBS task DAG."""
    artifacts: dict[str, XbstrapArtifact] = dict()
    nodes: dict[str, XbstrapNode] = dict()

    # First, collect all products.
    for job in raw_dag.values():
        products = job.products
        for type, artifact in itertools.chain(
            ((ArtifactType.TOOL, a) for a in products.tools),
            ((ArtifactType.PACKAGE, a) for a in products.pkgs),
        ):
            # Pacify mypy, it doesn't deduce the literal TOOL and PACKAGE.
            assert type != ArtifactType.FILE
            xbbs_artifact = XbstrapArtifact(
                is_up_to_date=job.up2date,
                data=XbstrapArtifactData(
                    artifact_type=type,
                    name=artifact.name,
                    version=artifact.version,
                    architecture=artifact.architecture,
                ),
            )
            assert xbbs_artifact.identifier not in artifacts
            artifacts[xbbs_artifact.identifier] = xbbs_artifact

        # Files require special handling.
        for file_artifact in products.files:
            xbbs_artifact = XbstrapArtifact(
                is_up_to_date=job.up2date,
                data=XbstrapArtifactFileData(
                    artifact_type=ArtifactType.FILE,
                    name=file_artifact.name,
                    architecture=file_artifact.architecture,
                ),
            )
            assert xbbs_artifact.identifier not in artifacts
            artifacts[xbbs_artifact.identifier] = xbbs_artifact

    # Then, we build up all the nodes.
    for job_slug, job in raw_dag.items():

        def _iter_artifacts(table: PipelineJobNeeds | PipelineJobProducts) -> T.Iterator[str]:
            def _get_artifact_pairs() -> T.Generator[tuple[ArtifactType, str, str], None, None]:
                yield from ((ArtifactType.TOOL, a.name, a.architecture) for a in table.tools)
                yield from ((ArtifactType.PACKAGE, a.name, a.architecture) for a in table.pkgs)
                if isinstance(table, PipelineJobProducts):
                    yield from ((ArtifactType.FILE, a.name, a.architecture) for a in table.files)

            return (artifact_identifier(t, a, n) for (t, n, a) in _get_artifact_pairs())

        node = XbstrapNode(
            identifier=job_slug,
            is_unstable=job.unstable,
            is_up_to_date=job.up2date,
            products=frozenset(_iter_artifacts(job.products)),
            dependencies=frozenset(_iter_artifacts(job.needed)),
            required_capabilities=frozenset(job.capabilities),
        )
        nodes[node.identifier] = node

    return XbstrapGraph(nodes=nodes, artifacts=artifacts)
