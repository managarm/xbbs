# Artifact upload and download.
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

from urllib.parse import quote

import aiohttp

import xbbs.utils.str as xbu_str
from xbbs.buildsystem import ArtifactHelper
from xbbs.utils.logging.build_logger import BuildLogger


class ArtifactHelperImpl(ArtifactHelper):
    def __init__(
        self,
        client: aiohttp.ClientSession,
        execution_id: str,
        repo_url_path: str,
        logger: BuildLogger,
    ) -> None:
        self.client = client
        self.execution_id = execution_id
        self.repo_url_path = repo_url_path
        self.logger = logger

    async def send_artifact(self, artifact_id: str, artifact_location: str) -> None:
        self.logger.info(f"Uploading artifact {artifact_id} from filename {artifact_location}")
        with open(artifact_location, mode="rb") as artifact:
            await self.client.post(
                f"/worker/artifact/{quote(self.execution_id, '')}/{quote(artifact_id, '')}",
                data=artifact,
            )

    async def get_repo_file(self, path: str, local_path: str) -> None:
        self.logger.info(f"Downloading artifact {path} into {local_path}")
        async with self.client.get(xbu_str.fuse_with_slashes(self.repo_url_path, path)) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as local:
                while data := await resp.content.read(128 * 1024):
                    local.write(data)
