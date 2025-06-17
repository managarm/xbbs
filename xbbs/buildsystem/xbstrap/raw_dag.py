# Models for the DAG output from xbstrap-pipeline.
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
This module contains models for data output by ``xbstrap-pipeline``.
"""

from pydantic import BaseModel
from pydantic.type_adapter import TypeAdapter


class PipelineArtifact(BaseModel):
    """An artifact as part of an ``xbstrap-pipeline`` job."""

    name: str
    """The name of this artifact."""

    version: str
    """Its version number."""

    architecture: str
    """The architecture it's built for."""


class PipelineFileArtifact(BaseModel):
    """
    File artifact as a part of an ``xbstrap-pipeline`` job.  Slightly different to the
    other kinds of artifacts in structure (notably, lacks a version.)
    """

    name: str
    """The name of this artifact."""

    filepath: str
    """Absolute path to this file."""

    architecture: str
    """The architecture it's built for."""


class PipelineJobProducts(BaseModel):
    """Products of one node in the graph ``xbstrap-pipeline`` generates."""

    tools: list[PipelineArtifact]
    """Tools produced by this node."""

    pkgs: list[PipelineArtifact]
    """Packages produced by this node."""

    files: list[PipelineFileArtifact]
    """Files produced by this node."""


class PipelineJobNeeds(BaseModel):
    """Dependencies of a node in the graph ``xbstrap-pipeline`` generates."""

    tools: list[PipelineArtifact]
    """Tools needed to start executing this node."""

    pkgs: list[PipelineArtifact]
    """Packages needed to start executing this node."""


class PipelineJob(BaseModel):
    """A single graph node produced by ``xbstrap-pipeline``."""

    up2date: bool
    """
    Whether this job is already up-to-date.  If it is, all its packages are already
    produced and placed into the repository during init.
    """
    unstable: bool
    """
    If ``True``, this job failing should not mark the entire build as failing.
    """

    capabilities: set[str]
    """Set of capabilities needed for a worker to run this job."""

    products: PipelineJobProducts
    """Set of products of this node."""

    needed: PipelineJobNeeds
    """Set of dependencies of this node."""


def parse_raw_dag(input: str | bytes) -> dict[str, PipelineJob]:
    """Parses out a raw DAG produced by ``xbstrap-pipeline``."""
    return TypeAdapter(dict[str, PipelineJob]).validate_json(input)
