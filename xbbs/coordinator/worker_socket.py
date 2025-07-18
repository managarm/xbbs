# Handle worker comms.
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
This module contains logic for handling communication with the workers.
"""

import logging
import os
import tempfile

from aiohttp import web
from werkzeug.security import safe_join

import xbbs.data.messages as xbm

from .coordinator_state import get_coord_state
from .ws_tracker import WEBSOCKET_TRACKER_KEY

blueprint = web.RouteTableDef()
logger = logging.getLogger(__name__)


@blueprint.get("/worker")
async def handle_worker_socket(request: web.Request) -> web.WebSocketResponse:
    coord = get_coord_state(request.app)
    ws = web.WebSocketResponse()
    ws_tracker = request.app[WEBSOCKET_TRACKER_KEY]
    await ws.prepare(request)
    ws_tracker.add(ws)

    worker = await coord.allocate_worker_tracker(ws)
    logger.debug("worker %r connected", worker)
    try:
        while not ws.closed:
            try:
                ws_msg = await ws.receive(timeout=90)
                logger.debug("received %r", ws_msg)
            except TimeoutError:
                # Worker died.
                logger.debug("worker %r timed out", worker)
                await ws.close()
                break

            if ws_msg.type in (web.WSMsgType.CLOSING, web.WSMsgType.CLOSE):
                # Worker decided to leave.
                break
            if ws_msg.type != web.WSMsgType.BINARY:
                # Client misbehaving.
                await ws.close()
                break

            try:
                msg = xbm.deserialize(ws_msg.data)
            except Exception:
                # Again, client misbehaviour.  TODO(arsen): catch only validation errors
                # and log them as debug.
                await ws.close()
                break

            match msg:
                case xbm.heartbeat.WorkerHeartbeat(hostname=hostname, load_avg=load):
                    worker.update_status(hostname, load)
                case xbm.tasks.TaskRequest(capabilities=caps):
                    if curr_exec := worker.current_execution:
                        worker.current_execution = None
                        coord.abnormally_fail_execution(curr_exec)
                    await worker.start_wait_for_task(caps)
                case xbm.tasks.LogMessage(execution_id=execution, log_line=data):
                    coord.write_log_data(execution, data)
                case xbm.tasks.TaskDone(execution_id=execution, run_time=rt, success=True):
                    worker.current_execution = None
                    await coord.succeed_execution(execution, rt)
                case xbm.tasks.TaskDone(execution_id=execution, run_time=rt, success=False):
                    worker.current_execution = None
                    await coord.fail_execution(execution, rt)
    finally:
        ws_tracker.remove(ws)
        await coord.remove_worker(worker)

    return ws


@blueprint.post("/worker/artifact/{execution}/{artifact_id}")
async def upload_artifact(req: web.Request) -> web.Response:
    coord = get_coord_state(req.app)
    execution = req.match_info["execution"]
    artifact_id = req.match_info["artifact_id"]
    logger.debug("artifact upload: %r/%r", execution, artifact_id)
    os.makedirs(coord.collection_directory, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb+", delete=False, dir=coord.collection_directory
    ) as artifact_file:
        try:
            while contents := await req.content.read(128 * 1024):
                artifact_file.write(contents)

            # File has been read into artifact_file.
            if not await coord.deposit_artifact_for(execution, artifact_id, artifact_file.name):
                try:
                    os.unlink(artifact_file.name)
                except FileNotFoundError:
                    pass
        except:  # noqa: E722
            try:
                os.unlink(artifact_file.name)
            except FileNotFoundError:
                pass
            raise

    return web.Response()


@blueprint.get("/repos/{execution_id}/{repo_file:.*}")
async def access_repo(req: web.Request) -> web.FileResponse:
    execution = req.match_info["execution_id"]

    repo_file = req.match_info["repo_file"]

    coord = get_coord_state(req.app)
    build = await coord.get_build_by_execution(execution)
    if build is None:
        raise web.HTTPNotFound()

    file_path = safe_join(build.build_dir, "repo", repo_file)
    if file_path is None:
        raise web.HTTPNotFound()

    return web.FileResponse(path=file_path)
