# xbbs worker
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

import asyncio
import logging
import os
import tempfile
import time

import aiohttp

import xbbs.data.config as config
import xbbs.data.messages as xbd_messages
import xbbs.utils.logging as xbu_logging
import xbbs.utils.pipe as xbu_pipe
import xbbs.utils.str as xbu_str

from .artifact_helper import ArtifactHelperImpl
from .builder import build_task

logger = logging.getLogger(__name__)


async def _do_log_forwarding(
    execution_id: str, socket: aiohttp.ClientWebSocketResponse, log_r_fd: int
) -> None:
    """
    Forward log messages written into the write side of :code:`log_r_fd`.

    Args:
      log_r_fd: Read end of a pipe whose contents to forward to the coordinator.
    """
    log_r = os.fdopen(log_r_fd, buffering=1, encoding="utf-8", errors="backslashreplace")
    async with xbu_pipe.pipe_text_reader(log_r) as log_reader:
        async for log_line in log_reader:
            await socket.send_bytes(
                xbd_messages.serialize(
                    xbd_messages.tasks.LogMessage(
                        msg_type="L", log_line=log_line, execution_id=execution_id
                    )
                )
            )


async def execute_job(
    client: aiohttp.ClientSession,
    task: xbd_messages.tasks.TaskResponse,
    worker_cfg: config.WorkerConfig,
    socket: aiohttp.ClientWebSocketResponse,
) -> None:
    """
    Sets up the environment for executing jobs and delegates to
    :py:func:``builder.do_build``.
    """
    try:
        start = time.monotonic()
        (log_r_fd, log_w_fd) = os.pipe()
        with tempfile.TemporaryDirectory(dir=worker_cfg.work_root) as work_dir:
            async with asyncio.TaskGroup() as build_group:
                build_group.create_task(_do_log_forwarding(task.execution_id, socket, log_r_fd))
                del log_r_fd
                log_w = os.fdopen(log_w_fd, "w", buffering=1)
                logger = xbu_logging.build_logger.BuildLogger(log_w)
                builder_future = build_group.create_task(
                    build_task(
                        work_dir,
                        ArtifactHelperImpl(
                            client,
                            task.execution_id,
                            task.repo_url_path,
                            logger,
                        ),
                        task,
                        logger,
                        xbu_str.fuse_with_slashes(worker_cfg.coordinator_url, task.repo_url_path),
                    )
                )
                # TODO(arsen): can this close deadlock due to flushing?
                builder_future.add_done_callback(lambda _: log_w.close())
                # TODO(arsen): needs extra messsage processing for
                # cancel/coordinator leaving/dying

        # At this point, builder_future is guaranteed to be complete.
        is_success = await builder_future
        await socket.send_bytes(
            xbd_messages.serialize(
                xbd_messages.tasks.TaskDone(
                    msg_type="DONE!",
                    success=is_success,
                    run_time=time.monotonic() - start,
                    execution_id=task.execution_id,
                )
            )
        )
    except Exception:
        # Job failed to some exception.
        logger.exception(f"execution {task.execution_id} failed")
        await socket.send_bytes(
            xbd_messages.serialize(
                xbd_messages.tasks.TaskDone(
                    msg_type="DONE!",
                    success=False,  # this branch is unreachable on success
                    run_time=time.monotonic() - start,
                    execution_id=task.execution_id,
                )
            )
        )


async def send_heartbeat(coordinator_socket: aiohttp.ClientWebSocketResponse) -> None:
    """Send a heartbeat message over ``coordinator_socket``."""
    await coordinator_socket.send_bytes(
        xbd_messages.serialize(xbd_messages.heartbeat.WorkerHeartbeat.make_for_current_machine())
    )


async def heartbeat(coordinator_socket: aiohttp.ClientWebSocketResponse) -> None:
    try:
        while not coordinator_socket.closed:
            await send_heartbeat(coordinator_socket)
            # Send every 15 seconds or so.
            await asyncio.sleep(15)
    except aiohttp.ClientError:
        logger.exception("failed to send heartbeat to coordinator")


async def attach_to_coordinator(
    client: aiohttp.ClientSession,
    worker_cfg: config.WorkerConfig,
) -> None:
    """
    Send tasks requests and heartbeats, receive tasks, and execute them, sending back
    artifacts and logs.  Raises only for connection errors, handling job errors
    internally.
    """
    async with (
        asyncio.TaskGroup() as connection_group,
        client.ws_connect("/worker") as coordinator_socket,
    ):
        # Identify immediately.  Important to do this first so the coordinator always knows our
        # name.
        await send_heartbeat(coordinator_socket)
        # Start heartbeating in the background also.
        hb_task = connection_group.create_task(heartbeat(coordinator_socket))

        while True:
            # Job loop.
            await coordinator_socket.send_bytes(
                xbd_messages.serialize(
                    xbd_messages.tasks.TaskRequest(
                        msg_type="BORED", capabilities=worker_cfg.capabilities
                    )
                )
            )

            # Receive and process job control messages.
            message = xbd_messages.deserialize(await coordinator_socket.receive_bytes())
            assert isinstance(message, xbd_messages.tasks.TaskResponse)
            # TODO(arsen): make execute_job happen asynchronously so that we can receive
            # more messages later.
            await execute_job(client, message, worker_cfg, coordinator_socket)

        # Supress unused warning.
        del hb_task


async def amain(cfg: config.WorkerConfig) -> None:
    async with aiohttp.ClientSession(base_url=cfg.coordinator_url) as client:
        # The worker initiates and keeps a single connection to the coordinator alive.
        # This is used to exchange heartbeats and load information, and requests for
        # jobs.  When a worker is given a job, it sends back artifacts and logs produced
        # during that job.  If this connection fails at any point, any current job is
        # abandoned and the worker attempts to reconnect.  The worker will keep doing so
        # as long as necessary.
        while True:
            try:
                await asyncio.shield(attach_to_coordinator(client, cfg))
            except Exception:
                # TODO(arsen): exponential backoff
                logger.exception("coordinator connection failed")
                await asyncio.sleep(30)


def main() -> None:
    worker_config = config.load_and_validate_config("worker.toml", config.WorkerConfig)
    xbu_logging.apply_logging_config(worker_config.log)
    logger.debug("config loaded: %r", worker_config)
    os.makedirs(worker_config.work_root, exist_ok=True)

    asyncio.run(amain(worker_config))
