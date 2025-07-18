# Build system independent package and artifact graph.
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
This module contains a build system independent model for xbbs dependency graphs.
"""

import enum
import typing as T


class Artifact(T.Protocol):
    """
    A protocol that artifacts in a graph must satisfy.  Provides a way for xbbs to
    identify each artifact.

    This structure shall remain immutable.
    """

    @property
    def identifier(self) -> str:
        """Unique identifier for this artifact.  This field ought to be final."""

    @property
    def is_up_to_date(self) -> bool:
        """
        ``True`` iff this artifact is up-to-date from a previous build.  Used for
        incremental builds.
        """

    @property
    def repo_file_path(self) -> str:
        """
        File path relative to the root of the repository directory where this artifact
        will be stored.
        """


class NodeState(enum.Enum):
    """States a graph node can be in"""

    UP_TO_DATE = "UP_TO_DATE"
    """Node was already up-to-date when we started."""
    WAITING = "WAITING"
    """Node dependencies are not yet built."""
    READY = "READY"
    """Node is ready to be scheduled."""
    RUNNING = "RUNNING"
    """
    Pseudo-state used by ``read_job_graph`` for states for which there exists a running
    execution.
    """
    FAILED = "FAILED"
    """Node build failed."""
    FAILED_ABNORMALLY = "FAILED_ABNORMALLY"
    """Node failed abnormally."""
    UNSATISFIABLE = "UNSATISFIABLE"
    """A dependency of the node will never be built."""
    SUCCEEDED = "SUCCEEDED"
    """Managed to run to completion."""

    @property
    def is_final(self) -> bool:
        """
        Returns:
          ``True`` if a node cannot exit this state.
        """
        return self in (
            NodeState.SUCCEEDED,
            NodeState.FAILED,
            NodeState.FAILED_ABNORMALLY,
            NodeState.UNSATISFIABLE,
            NodeState.UP_TO_DATE,
        )

    @property
    def is_success(self) -> bool:
        """
        Returns:
          ``True`` if the node is considered successfully built.
        """
        return self in (
            NodeState.SUCCEEDED,
            NodeState.UP_TO_DATE,
        )


class Node(T.Protocol):
    """
    A protocol that nodes in a graph must satisfy.  Provides graph edges, as
    dependencies on artifacts, as well as a way to identify each node.

    This structure shall remain immutable.
    """

    @property
    def identifier(self) -> str:
        """Unique identifier for this dependency.  This field ought to be final"""

    @property
    def dependencies(self) -> frozenset[str]:
        """
        Identifiers of artifacts this node depends on.  It will be started when all
        dependencies are received.
        """

    @property
    def products(self) -> frozenset[str]:
        """
        Identifiers of artifacts this node produces.  Other nodes effectively have edges
        into these products.  Must not overlap with ``dependencies``, or have a cycle
        that leads back into ``dependencies``.  Must have no overlap with ``products``
        of any other set.
        """

    @property
    def is_up_to_date(self) -> bool:
        """
        ``True`` iff this node is up-to-date from a previous build.  Used for
        incremental builds.
        """

    @property
    def is_unstable(self) -> bool:
        """
        ``True`` if this task is considered unstable or flaky.  If the only failures of
        a build are such unstable failures, then a build is considered successful.
        """

    @property
    def required_capabilities(self) -> T.Iterable[str]:
        """
        List of capabilities the worker must have to take this task.
        """


class Graph(T.Protocol):
    """
    A complete graph.  Must be self-consistent and acyclic.  A graph is self-consistent
    if all nodes and artifacts in ``nodes`` and ``artifacts`` have identifiers matching
    their keys, and no nodes depend on an artifact that is not in ``artifacts``.  All
    artifact identifiers also must be products of exactly one node.

    This structure shall remain immutable.
    """

    @property
    def nodes(self) -> T.Mapping[str, Node]:
        """
        The set of all nodes of this graph.  Each node contains edges leaving it in its
        ``dependencies`` attribute.
        """

    @property
    def artifacts(self) -> T.Mapping[str, Artifact]:
        """The set of all artifacts of this graph."""


class GraphConsistencyError(ValueError):
    """
    Raised when a :py:class:`Graph` is detected to violate some constraint.
    """


def validate_graph(graph: Graph) -> None:
    """
    Check that a given graph is valid, per definition in :py:class:`Graph`.
    """
    # First, the easy stuff.  Check the identifiers.
    if any(name != artifact.identifier for (name, artifact) in graph.artifacts.items()):
        raise GraphConsistencyError("Artifact identifiers not consistent")
    if any(name != node.identifier for (name, node) in graph.nodes.items()):
        raise GraphConsistencyError("Artifact identifiers not consistent")

    # Verify that each artifact is produced by exactly one node.
    seen_artifacts = set[str]()
    for node in graph.nodes.values():
        for product in node.products:
            if product in seen_artifacts:
                raise GraphConsistencyError(f"Product {product!r} produced multiple times")
            seen_artifacts.add(product)

    orphan_artifact = next((x for x in graph.artifacts.keys() if x not in seen_artifacts), None)
    if orphan_artifact:
        raise GraphConsistencyError(f"Artifact {orphan_artifact!r} is orphaned (has no producer)")

    missing_artifact = next((x for x in seen_artifacts if x not in graph.artifacts.keys()), None)
    if missing_artifact:
        raise GraphConsistencyError(
            f"Artifact {missing_artifact!r} is missing (produced but not emitted)"
        )

    # TODO(arsen): check for cycles
