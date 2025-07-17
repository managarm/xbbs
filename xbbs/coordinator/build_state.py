# Reading and writing on-disk build state.
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
This module implements manipulating the on-disk build state, which is serialized as a
SQLite3 database.

Database schema
===============
The database contains the build artifacts, the task graph, as well as the status of each
task and artifact of the graph.

Artifact table
--------------
The ``artifact`` table is of the following format:

============== ===========
Column         Description
============== ===========
``identifier`` A unique string identifying this artifact.
``state``      State of this artifact.  May be ``UP_TO_DATE``, if it was already up to
               date when the build started, ``WAITING``, if it is not received yet,
               ``RECEIVED`` if it is received or ``MISSING`` if it was not received.
``product_of`` Identifier of the node producing this artifact.
``repo_path``  Path to this artifact in the build repository.

Node table
----------
The ``node`` table contains all the nodes of this build, and their statuses.  It is of
the following format:

============== ===========
Column         Description
============== ===========
``identifier`` A unique string identifying this node.
``state``      State of this node.  May be ``UP_TO_DATE``, if it was already up to
               date when the build started, ``WAITING``, if it is waiting on a dependency,
               ``READY``, if it is waiting on a worker, ``FAILED``, if the node failed normally,
               ``FAILED_ABNORMALLY``, if the node failed abnormally (e.g. timed out many times in a
               row, and was given up on), ``UNSATISFIABLE``, if a dependency failed to compute, or
               ``SUCCEEDED`` if it ran to completion with no issues.
``unstable``   ``true`` if a failure of this node can be ignored when determining
               whether a build failed.


Execution table
---------------
The execution table contains a list of all executions for each task node, mapping
nodes to execution IDs, and vice-versa.  These executions are ordered by ``ROWID``,
which is automatically tracked by SQLite.  It is of the following format:

============== ===========
Column         Description
============== ===========
``exec_id``    ID of this execution.  Also identifies the log file.  Unique.
``node_id``    The task for which this execution is responsible.
``state``      State this execution is in.  Can be ``IN_QUEUE``, if it enqueued but not
               yet sent, ``RUNNING`` if it has been sent, ``FAILED_ABNORMALLY``, if it
               failed abnormally (and hence can be retried), ``FAILED``, if it failed
               normally (and hence there's no point in retrying), or ``SUCCEEDED`` if
               the execution is successful.
``queue_time`` When this task was enqueued, as a ISO-8601 zoned timestamp.
``done_time``  When this task was considered done by the coordinator, as a ISO-8601
               zoned timestamp.
``run_time``   Worker-reported run time, in case of ``FAILED`` or ``SUCCEEDED``
               executions.  Measured in fractional seconds.
``builder``    Hostname of the builder, if the task was scheduled.

Dependencies table
------------------
The ``dependency`` table stores edges of the node graph.  It is of the following format:

=============== ===========
Column          Description
=============== ===========
``node_id``     Identifier of the node this edge is exiting.
``artifact_id`` Identifier of the artifact this node is flowing into.


Build table
-----------
The ``build`` table contains exactly one row, with attributes matching build metadata.
This key is named version, and might change in future versions.

It is of the following format:

============== ===========
Column         Description
============== ===========
``version``    Primary key.  Always ``1``.
``start_time`` ISO-8601 zoned timestamp of the moment the build started.
``end_time``   ISO-8601 zoned timestamp of the moment the build concluded.
``state``      What step is the build currently on?  May be ``SCHEDULED``, if the build
               is waiting to start, ``FETCHING`` if the build is currently fetching
               sources, ``UPDATING_MIRRORS``, if the build is updating mirrors,
               ``SETUP``, if the build is preparing, ``CALCULATING``, if the build is
               computing the task graph, ``RUNNING``, if the build is executing,
               ``SUCCEEDED`` if the build completed with no stable failures,
               ``FAILED`` if the build completed and there was a stable failure, or
               ``COORDINATOR_FAILED`` if the build terminated due to a failure on the
               coordinator.
``revision``   Git hash at which the build occurred.
"""

import enum
import os.path as path
import sqlite3
import typing as T
from dataclasses import dataclass
from datetime import datetime, timezone

from xbbs.buildsystem.dag import Graph, NodeState

if T.TYPE_CHECKING:
    # Circular dependency.
    from .execution import ExecutionState


def open_build_db(build_directory: str) -> sqlite3.Connection:
    return sqlite3.connect(path.join(build_directory, "status.db"))


def create_build_db(build_directory: str, start_time: datetime) -> None:
    """
    In the given directory, prepare a build status database.

    Assumes that the database does not yet exist.

    Args:
      start_time: The start time to record in the database.  Must be a timezone-aware
                  datetime object.
    """
    assert start_time.tzinfo

    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.executescript(
                r"""
            CREATE TABLE node (
                identifier TEXT NOT NULL PRIMARY KEY,
                state      TEXT NOT NULL CHECK (state IN ('UP_TO_DATE', 'WAITING',
                                                          'READY', 'FAILED', 'FAILED_ABNORMALLY',
                                                          'UNSATISFIABLE', 'SUCCEEDED')),
                unstable   INT NOT NULL
            );
            CREATE TABLE artifact (
                identifier TEXT NOT NULL PRIMARY KEY,
                state      TEXT NOT NULL CHECK (state IN ('UP_TO_DATE', 'WAITING',
                                                          'RECEIVED', 'MISSING')),
                product_of TEXT NOT NULL REFERENCES node (identifier),
                repo_path  TEXT NOT NULL
            );
            CREATE TABLE dependency (
                node_id     TEXT NOT NULL REFERENCES node (identifier),
                artifact_id TEXT NOT NULL REFERENCES artifact (identifier),
                PRIMARY KEY (node_id, artifact_id)
            );
            CREATE TABLE execution (
                exec_id     TEXT NOT NULL PRIMARY KEY,
                node_id     TEXT NOT NULL REFERENCES node (identifier),
                state       TEXT NOT NULL CHECK (state IN ('IN_QUEUE', 'RUNNING',
                                                           'FAILED_ABNORMALLY',
                                                           'FAILED', 'SUCCEEDED')),
                queue_time  TEXT NOT NULL,
                done_time   TEXT,
                run_time    REAL,
                builder     TEXT
            );
            CREATE TABLE build (
                version     INT NOT NULL CHECK (version = 1) PRIMARY KEY,
                start_time  TEXT NOT NULL,
                end_time    TEXT,
                revision    TEXT,
                state       TEXT NOT NULL CHECK (state IN ('SCHEDULED', 'FETCHING', 'SETUP',
                                                           'UPDATING_MIRRORS',
                                                           'CALCULATING', 'RUNNING',
                                                           'SUCCEEDED', 'FAILED',
                                                           'COORDINATOR_FAILED'))
            );
            """
            )
            conn.execute(
                r"""
                INSERT INTO build (version, start_time, state)
                            VALUES (1, ?, 'SCHEDULED');
                """,
                (start_time.isoformat(),),
            )
    finally:
        conn.close()


def set_scheduled(build_directory: str) -> None:
    """
    For the given build directory, set the build state to ``SCHEDULED``.
    """
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute("UPDATE build SET state = 'SCHEDULED'")
    finally:
        conn.close()


def set_build_fetching(build_directory: str) -> None:
    """
    For the given build directory, set the build state to ``FETCHING``.
    """
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute("UPDATE build SET state = 'FETCHING'")
    finally:
        conn.close()


def set_build_setup(build_directory: str, revision: str) -> None:
    """
    For the given build directory, set the build revision recorded in the build status
    database to ``revision``.  Sets the build state to ``SETUP``.
    """
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute("UPDATE build SET revision = ?, state = 'SETUP'", (revision,))
    finally:
        conn.close()


def set_build_calculating(build_directory: str) -> None:
    """
    For the given build directory, set the build state to ``CALCULATING``.
    """
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute("UPDATE build SET state = 'CALCULATING'")
    finally:
        conn.close()


def store_graph(build_directory: str, graph: "Graph") -> None:
    """
    For the given build directory, store the task graph and set the buil state to
    ``RUNNING``.
    """
    artifact_producer_index = dict[str, str]()
    for identifier, node in graph.nodes.items():
        for product in node.products:
            artifact_producer_index[product] = identifier

    conn = open_build_db(build_directory)
    try:
        with conn:
            # Store nodes.
            conn.executemany(
                "INSERT INTO node (identifier, state, unstable) VALUES (?,?,?)",
                (
                    (
                        identifier,
                        "UP_TO_DATE" if node.is_up_to_date else "WAITING",
                        node.is_unstable,
                    )
                    for (identifier, node) in graph.nodes.items()
                ),
            )

            # Store artifacts.
            conn.executemany(
                "INSERT INTO artifact (identifier, state, product_of, repo_path)"
                " VALUES (?,?,?,?)",
                (
                    (
                        identifier,
                        "UP_TO_DATE" if artifact.is_up_to_date else "WAITING",
                        artifact_producer_index[identifier],
                        artifact.repo_file_path,
                    )
                    for (identifier, artifact) in graph.artifacts.items()
                ),
            )

            # Store dependency edges.
            conn.executemany(
                "INSERT INTO dependency (node_id, artifact_id) VALUES (?, ?)",
                (
                    (node_id, artifact_id)
                    for (node_id, node) in graph.nodes.items()
                    for artifact_id in node.dependencies
                ),
            )
            conn.execute("UPDATE build SET state = 'RUNNING'")
    finally:
        conn.close()


def set_coordinator_failed(build_directory: str) -> None:
    """
    For the given build directory, set the build state to ``COORDINATOR_FAILED``.
    """
    conn = open_build_db(build_directory)
    try:
        with conn:
            end_time = datetime.now(timezone.utc)
            conn.execute(
                "UPDATE build SET state = 'COORDINATOR_FAILED', end_time = ?",
                (end_time.isoformat(),),
            )
    finally:
        conn.close()


def set_build_final_state(build_directory: str, succeeded: bool) -> None:
    """
    For the given build directory, set the build state to ``SUCCEEDED`` if
    ``succeeded``, otherwise ``FAILED``.
    """
    conn = open_build_db(build_directory)
    try:
        with conn:
            end_time = datetime.now(timezone.utc)
            conn.execute(
                "UPDATE build SET state = ?, end_time = ?",
                ("SUCCEEDED" if succeeded else "FAILED", end_time.isoformat()),
            )
    finally:
        conn.close()


def mark_artifact_received(build_directory: str, artifact_id: str) -> None:
    """Mark that artifact ``artifact_id`` was received."""
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                "UPDATE artifact SET state = 'RECEIVED' WHERE identifier = ?",
                (artifact_id,),
            )
    finally:
        conn.close()


def mark_artifact_missing(build_directory: str, artifact_id: str) -> None:
    """Mark that artifact ``artifact_id`` was not received."""
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                "UPDATE artifact SET state = 'MISSING' WHERE identifier = ?",
                (artifact_id,),
            )
    finally:
        conn.close()


def add_execution(
    build_directory: str, execution_id: str, node_id: str, start_time: datetime
) -> None:
    """Records that some execution was started."""
    assert start_time.tzinfo
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO execution (exec_id, node_id, state, queue_time)
                            VALUES (?, ?, 'IN_QUEUE', ?)
                """,
                (execution_id, node_id, start_time.isoformat()),
            )
    finally:
        conn.close()


def set_exec_abnormally_failed(
    build_directory: str, execution_id: str, done_time: datetime
) -> None:
    """Records that an execution failed abnormally."""
    assert done_time.tzinfo
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                """
                UPDATE execution SET state = 'FAILED_ABNORMALLY', done_time = ?
                                 WHERE exec_id = ?
                """,
                (done_time.isoformat(), execution_id),
            )
    finally:
        conn.close()


def set_exec_running(build_directory: str, execution_id: str, builder: str) -> None:
    """Records that an execution is now running."""
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                """
                UPDATE execution SET state = 'RUNNING', builder = ?
                                 WHERE exec_id = ? AND state = 'IN_QUEUE'
                """,
                (
                    builder,
                    execution_id,
                ),
            )
    finally:
        conn.close()


def set_exec_failed(
    build_directory: str, execution_id: str, done_time: datetime, run_time: float
) -> None:
    """Records that an execution failed normally."""
    assert done_time.tzinfo
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                """
                UPDATE execution SET state = 'FAILED', done_time = ?, run_time = ?
                                 WHERE exec_id = ?
                """,
                (done_time.isoformat(), run_time, execution_id),
            )
    finally:
        conn.close()


def set_exec_success(
    build_directory: str, execution_id: str, done_time: datetime, run_time: float
) -> None:
    """Records that an execution success normally."""
    assert done_time.tzinfo
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                """
                UPDATE execution SET state = 'SUCCEEDED', done_time = ?, run_time = ?
                                 WHERE exec_id = ?
                """,
                (done_time.isoformat(), run_time, execution_id),
            )
    finally:
        conn.close()


def set_node_state(build_directory: str, node_id: str, new_state: "NodeState") -> None:
    """Records that node ``node_id`` is now in state ``new_state``."""
    conn = open_build_db(build_directory)
    try:
        with conn:
            conn.execute(
                """
                UPDATE node SET state = ? WHERE identifier = ?
                """,
                (new_state.value, node_id),
            )
    finally:
        conn.close()


# Reading
class BuildState(enum.Enum):
    SCHEDULED = "SCHEDULED"
    """The build is waiting to start."""
    FETCHING = "FETCHING"
    """The coordinator is fetching the distribution source code."""
    SETUP = "SETUP"
    """The coordinator is preparing the build root."""
    UPDATING_MIRRORS = "UPDATING"
    """The coordinator is updating mirrors."""
    CALCULATING = "CALCULATING"
    """The coordinator is computing the node graph."""
    RUNNING = "RUNNING"
    """The coordinator is delegating jobs."""
    SUCCEEDED = "SUCCEEDED"
    """The build finished successfully."""
    FAILED = "FAILED"
    """The build failed to build"""
    COORDINATOR_FAILED = "COORDINATOR_FAILED"
    """The build concluded due to a coordinator failure."""

    @property
    def is_final(self) -> bool:
        """``True`` for states in which the build is complete."""
        return self in (BuildState.SUCCEEDED, BuildState.FAILED, BuildState.COORDINATOR_FAILED)

    @property
    def is_success(self) -> bool:
        """``True`` if this build was a success."""
        return self == BuildState.SUCCEEDED


@dataclass
class DbBuild:
    """Overall build status."""

    state: BuildState
    """Current build state."""

    revision: str | None
    """Checked-out source revision."""

    start_time: datetime
    """Timezone-aware datetime object indicating when this build was started."""

    end_time: datetime | None
    """Timezone-aware datetime object indicating when this build finished."""


def read_build_object(conn: sqlite3.Connection) -> DbBuild:
    """
    Given a build DB connection, load the build object for this build.
    """
    cur = conn.cursor()
    state, rev, start_ts, end_ts = cur.execute(
        "SELECT state, revision, start_time, end_time FROM build"
    ).fetchone()

    return DbBuild(
        state=BuildState[state],
        revision=rev,
        start_time=datetime.fromisoformat(start_ts),
        end_time=end_ts and datetime.fromisoformat(end_ts),
    )


@dataclass
class DbExecution:
    """A single execution."""

    id: str
    """Execution ID of this execution"""

    node_id: str
    """Node ID of this execution"""

    state: "ExecutionState"
    """How far along is this execution?"""

    builder: str | None
    """Builder this job was scheduled on, if any."""

    queue_time: datetime
    """TZ-aware datetime object noting when this execution started.  In coordinator time."""

    done_time: datetime | None
    """TZ-aware datetime object noting when this execution finished.  In coordinator time."""

    run_time: float | None
    """Time, in fractional seconds, that the execution took on the worker."""


def _format_exec(row: tuple[str, str, str, str, str, str | None, float | None]) -> DbExecution:
    from .execution import ExecutionState

    exec_id, node_id, state, builder, queue_time, done_time, run_time = row
    return DbExecution(
        id=exec_id,
        node_id=node_id,
        state=ExecutionState[state],
        builder=builder,
        queue_time=datetime.fromisoformat(queue_time),
        done_time=datetime.fromisoformat(done_time) if done_time else None,
        run_time=run_time,
    )


def read_executions(conn: sqlite3.Connection, node: str | None = None) -> list[DbExecution]:
    """
    Read a list of all executions of the given build DB, in chronological order.

    If ``node`` is given, restricts to only executions of ``node``.
    """
    cur = conn.cursor()
    if node is not None:
        return list(
            map(
                _format_exec,
                cur.execute(
                    """
                    SELECT exec_id, node_id, state, builder, queue_time, done_time, run_time
                           FROM execution WHERE node_id = ?
                    """,
                    (node,),
                ).fetchall(),
            )
        )

    return list(
        map(
            _format_exec,
            cur.execute(
                """
                SELECT exec_id, node_id, state, builder, queue_time, done_time, run_time
                       FROM execution
                """
            ).fetchall(),
        )
    )


def read_one_execution(conn: sqlite3.Connection, execution_id: str) -> DbExecution | None:
    """Read an execution of the given build DB."""
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT exec_id, node_id, state, builder, queue_time, done_time, run_time FROM execution
               WHERE exec_id = ?
        """,
        (execution_id,),
    ).fetchone()
    if not row:
        return None
    return _format_exec(row)


class ArtifactState(enum.Enum):
    UP_TO_DATE = "UP_TO_DATE"
    """The artifact was up-to-date and does not need rebuilding."""
    WAITING = "WAITING"
    """The artifact has not been produced yet."""
    RECEIVED = "RECEIVED"
    """The artifact has been received from a worker."""
    MISSING = "MISSING"
    """The artifact was never delivered.  Perhaps it failed to build?"""


@dataclass
class DbArtifact:
    """A single artifact."""

    identifier: str
    """Unique string identifying this artifact."""
    state: ArtifactState
    """Current state of this artifact."""
    repo_path: str
    """In-repository path to this artifact."""
    product_of: str
    """ID of the job that produces this artifact."""


@dataclass
class DbJob:
    """A single jobnode."""

    identifier: str
    """Unique string identifying this artifact."""
    state: NodeState
    """Current state of this node."""
    is_unstable: bool
    """
    Whether or not this node is considered unstable.  Unstable node failures do not fail builds.
    """
    products: list[str]
    """Artifacts produced by this job."""
    dependencies: list[str]
    """Artifacts required by this job."""


@dataclass
class DbGraph:
    """A job graph loaded from the database."""

    artifacts: dict[str, DbArtifact]
    """All artifacts in this build."""
    jobs: dict[str, DbJob]
    """All jobs in this build."""


def read_job_graph(conn: sqlite3.Connection) -> DbGraph:
    """Given a build status DB connection, load the job graph stored in it."""
    db_graph = DbGraph(dict(), dict())
    with conn:
        cur = conn.cursor()
        for id, state, unstable in cur.execute(
            "SELECT identifier, state, unstable FROM node"
        ).fetchall():
            assert id not in db_graph.jobs
            db_graph.jobs[id] = DbJob(
                identifier=id,
                state=NodeState[state],
                is_unstable=unstable,
                products=list(),
                dependencies=list(),
            )

        cur = conn.cursor()
        for id, state, product_of, repo_path in cur.execute(
            "SELECT identifier, state, product_of, repo_path FROM artifact"
        ).fetchall():
            assert id not in db_graph.artifacts
            db_graph.artifacts[id] = DbArtifact(
                identifier=id,
                repo_path=repo_path,
                state=ArtifactState[state],
                product_of=product_of,
            )
            db_graph.jobs[product_of].products.append(id)

        cur = conn.cursor()
        for node_id, artifact_id in cur.execute(
            "SELECT node_id, artifact_id FROM dependency"
        ).fetchall():
            assert artifact_id in db_graph.artifacts
            db_graph.jobs[node_id].dependencies.append(artifact_id)

    return db_graph
