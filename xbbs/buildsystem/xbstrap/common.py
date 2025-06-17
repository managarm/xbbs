# Stuff used by both the xbstrap coordinator and worker.
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
This module contains stuff used by both the xbstrap coordinator and worker.
"""

import typing as T

from pydantic import BaseModel, Field

from .dag import XbstrapArtifact, XbstrapNode

CommitObjectValueKey: T.TypeAlias = T.Literal["rolling_id"] | T.Literal["fixed_commit"]
"""Keys for each package in the commit object."""

CommitObject: T.TypeAlias = dict[str, dict[CommitObjectValueKey, str]]
"""
A dictionary mapping each package to the rolling ID and commit it was resolved to at
init time.
"""


class XbstrapTask(BaseModel):
    """
    Contains information necessary to for the worker to execute an ``xbstrap`` task.
    """

    referenced_artifacts: dict[str, XbstrapArtifact]
    """
    Artifacts needed or produced by this task.  Used to know what to download and
    install before invoking ``xbstrap`` to run the job, and to construct paths to
    artifacts that need to be uploaded.
    """

    task: XbstrapNode
    """
    Task to build.
    """

    commits_object: CommitObject
    """
    Pinned commits and versions of rolling packages in this build.
    """

    xbps_keys: dict[str, bytes]
    """
    Pubkeys used to sign packages in this repository.
    """

    distfile_path: str | None
    """
    Path within the source tree which to merge into the build tree.
    """

    mirror_root: str | None = Field(default=None)
    """
    Root URL for the Git mirrors.
    """

    def get_dependencies(self) -> T.Generator[XbstrapArtifact, None, None]:
        """
        Returns:
          A generator that yields all the dependencies as :py:class:`XbstrapArtifact`
          objects.
        """
        return (self.referenced_artifacts[dep] for dep in self.task.dependencies)
