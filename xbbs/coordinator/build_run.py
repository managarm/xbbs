# Runs a coordinator build.
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
This module implements the :py:meth:`xbbs.coordinator.project.Build.run` method.
"""

import asyncio
import datetime
import os
import os.path as path
import shutil
import typing as T

import xbbs.data.messages.tasks as xbm_tasks
import xbbs.utils.logging as xbu_log
import xbbs.utils.proc as xbu_proc
from xbbs.buildsystem import create_build_system_factory, dag
from xbbs.constants import MAX_NODE_RETRIES

from .build_state import (
    add_execution,
    create_build_db,
    mark_artifact_missing,
    mark_artifact_received,
    set_build_final_state,
)
from .execution import Execution, ExecutionState

if T.TYPE_CHECKING:
    from .coordinator_state import CoordinatorState
    from .project import Build


def _mark_fail(self: "Build", graph: dag.Graph, product_id: str) -> None:
    try:
        mark_artifact_missing(self.build_dir, product_id)
    except Exception:
        self.log_io.exception(f"failed to mark artifact {product_id} as failed")
        pass
    for node in graph.nodes.values():
        if self._node_states[node.identifier] == dag.NodeState.UNSATISFIABLE:
            # Visited already.
            continue
        if product_id not in node.dependencies:
            continue

        assert self._node_states[node.identifier] in (
            dag.NodeState.WAITING,
            # If it was never to run
            dag.NodeState.UP_TO_DATE,
        ), f"node {node.identifier} cannot be in state {self._node_states[node.identifier]}"
        self.set_node_state(node.identifier, dag.NodeState.UNSATISFIABLE)
        for product in node.products:
            _mark_fail(self, graph, product)

    self._has_more_artifacts.set()


async def _enqueue_node(
    self: "Build",
    graph: dag.Graph,
    node: dag.Node,
    alloc_execution: T.Callable[["Build"], T.Coroutine[T.Any, T.Any, "Execution"]],
    send_task: T.Callable[[xbm_tasks.TaskResponse, set[str]], T.Coroutine[T.Any, T.Any, None]],
) -> None:
    self.log_io.info(f"Node {node.identifier} is ready to build")
    buildsystem_data = await self._buildsystem.serialize_task(node)

    for retry in range(MAX_NODE_RETRIES):
        execution = await alloc_execution(self)
        add_execution(  # TODO(arsen): handle error
            self.build_dir,
            execution.execution_id,
            node.identifier,
            datetime.datetime.now(datetime.timezone.utc),
        )
        task_msg = xbm_tasks.TaskResponse(
            msg_type="WORK!",
            execution_id=execution.execution_id,
            git=self.project_config.git,
            revision=self.revision,
            repo_url_path=f"/repos/{execution.execution_id}",
            buildsystem=self.project_config.buildsystem.type_enum(),
            buildsystem_specific=buildsystem_data,
        )
        await send_task(
            task_msg,
            set(node.required_capabilities),
        )

        # Wait for it to finish.
        await execution.done.wait()
        self.log_io.info(
            f"Execution {execution.execution_id} of {node.identifier}"
            f" done as {execution.state.name}"
        )

        # If it succeeded, we're done.
        if execution.state == ExecutionState.SUCCEEDED:
            break

        # If the failure is not abnormal, we won't retry.
        if execution.state == ExecutionState.FAILED:
            break

        # Otherwise, it is an abnormal failure to be retried
        assert execution.state == ExecutionState.FAILED_ABNORMALLY
        execution.clean_up_artifacts()

    # In the end, we need to process the artifacts...
    for product in node.products:
        product_file = execution.received_artifacts.get(product, None)
        if product_file is None:
            _mark_fail(self, graph, product)
            continue
        try:
            await self._buildsystem.deposit_artifact(product, product_file)
            self._done_artifacts.add(product)
            self._has_more_artifacts.set()
            mark_artifact_received(self.build_dir, product)
        except Exception:
            self.log_io.exception(f"failed to deposit artifact {product} into {product_file}")
            if product not in self._done_artifacts:
                _mark_fail(self, graph, product)
            continue

    # ... and mark the job appropriately
    self.set_node_state(
        node.identifier,
        {
            # Failed instantly.
            ExecutionState.FAILED: dag.NodeState.FAILED,
            # Ran out of retries.
            ExecutionState.FAILED_ABNORMALLY: dag.NodeState.FAILED_ABNORMALLY,
            # Ran to completion.
            ExecutionState.SUCCEEDED: dag.NodeState.SUCCEEDED,
        }[execution.state],
    )


def _find_work(
    self: "Build",
    graph: dag.Graph,
    executions_group: asyncio.TaskGroup,
    alloc_execution: T.Callable[["Build"], T.Coroutine[T.Any, T.Any, "Execution"]],
    send_task: T.Callable[[xbm_tasks.TaskResponse, set[str]], T.Coroutine[T.Any, T.Any, None]],
) -> bool:
    some_waiting = False
    for node in graph.nodes.values():
        if self._node_states[node.identifier] != dag.NodeState.WAITING:
            continue
        some_waiting = True

        if not node.dependencies.issubset(self._done_artifacts):
            continue

        # This node is ready.
        self.set_node_state(node.identifier, dag.NodeState.READY)
        executions_group.create_task(_enqueue_node(self, graph, node, alloc_execution, send_task))

    return some_waiting


async def _solve_graph(
    self: "Build",
    graph: dag.Graph,
    alloc_execution: T.Callable[["Build"], T.Coroutine[T.Any, T.Any, "Execution"]],
    send_task: T.Callable[[xbm_tasks.TaskResponse, set[str]], T.Coroutine[T.Any, T.Any, None]],
) -> None:
    async with asyncio.TaskGroup() as executions_group:
        while True:
            if not _find_work(self, graph, executions_group, alloc_execution, send_task):
                # Out of tasks to wait for.
                break
            await self._has_more_artifacts.wait()
            self._has_more_artifacts.clear()


async def _run(
    self: "Build",
    alloc_execution: T.Callable[["Build"], T.Coroutine[T.Any, T.Any, "Execution"]],
    send_task: T.Callable[[xbm_tasks.TaskResponse, set[str]], T.Coroutine[T.Any, T.Any, None]],
) -> bool:
    """
    Returns:
      ``True`` if the build succeeded, otherwise ``False``.
    """
    # Does not handle errors, those are handled in ``run``.

    async def _cmd(cwd: str, *args: str) -> None:
        await xbu_proc.do_command(self.log_io, *args, cwd=cwd)

    async def _call(cwd: str, *args: str) -> bytes:
        return await xbu_proc.get_command_output(self.log_io, *args, cwd=cwd)

    # Prepare job directory.
    self.log_io.info("Starting build...")
    create_build_db(self.build_dir, datetime.datetime.now(datetime.timezone.utc))
    work_dir = path.join(self.build_dir, "work")
    repo_dir = path.join(self.build_dir, "repo")
    src_dir = path.join(self.build_dir, "src")
    for subdir in (work_dir, repo_dir, src_dir):
        os.makedirs(subdir)

    # Clone the repository.
    self.log_io.info(f"Fetching {self.project_config.git}")
    self.record_fetching()
    await _cmd(src_dir, "git", "clone", self.project_config.git, ".")
    revision = (await _call(src_dir, "git", "rev-parse", "HEAD")).decode().strip()
    self.log_io.info(f"Fetched revision {revision}")

    # Record we're done with fetching and have moved onto SETUP.
    self.record_start_setup(revision)

    # Prepare the build system.
    self._buildsystem = buildsystem = create_build_system_factory(
        self.project_config.buildsystem.type_enum()
    ).create_coordinator_side(
        self.log_io,
        self.build_id,
        src_dir,
        work_dir,
        repo_dir,
        # repo_dir of the previous build
        self.previous_build_dir and path.join(self.previous_build_dir, "repo"),
        self.project_config,
    )
    await buildsystem.prepare_job_directory()

    # Move into CALCULATING.
    self.log_io.info("Calculating package graph")
    self.record_setup_done()
    graph = await buildsystem.compute_job_graph()
    dag.validate_graph(graph)

    self.set_graph(graph)

    # We're now in RUNNING.  Time to follow the graph and see where it goes.
    await _solve_graph(self, graph, alloc_execution, send_task)

    # Alright.  Done.
    assert all(state.is_final for state in self._node_states.values())
    return all(
        node.is_unstable or self._node_states[node.identifier].is_success
        for node in self._graph.nodes.values()
    )


async def run(self: "Build", coordinator_state: "CoordinatorState") -> None:
    """
    Prepare and distribute jobs.
    """
    os.makedirs(self.log_dir)
    self.log_io = xbu_log.build_logger.BuildLogger(
        open(path.join(self.log_dir, "coordinator.log"), "w", buffering=1)
    )
    try:
        build_result = await _run(
            self,
            coordinator_state.alloc_execution,
            lambda task, caps: coordinator_state.add_task(task, caps, self.build_dir),
        )
        set_build_final_state(self.build_dir, build_result)
    except BaseException:
        try:
            self.mark_as_coordinator_fail()
        except Exception:
            self.log_io.exception("failed to store fault status")
        raise
    finally:
        self.log_io.out_stream.close()
        # Due to the TaskGroup executions_group, there are no executions to take care of
        # at this point.
        for subtree in ("src", "work"):
            shutil.rmtree(path.join(self.build_dir, subtree))
