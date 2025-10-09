# CLI interface to controlling the coordinator
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
``xbbs-cli`` is a tool for interacting with and controlling the ``xbbs`` coordinator.
"""

import argparse
import asyncio
import sys
import typing as T

import aiohttp

import xbbs.data.config as config
import xbbs.utils.argparse as xbu_cli
import xbbs.utils.http as xbu_http

argparser = xbu_cli.create_root_parser(__doc__)
argparser.add_argument(
    "--coordinator-url",
    help="Base URL the coordinator is listening on.  If missing, read from configuration.",
    action="store",
    default=None,
)
subcommands = argparser.add_subparsers(dest="command")


# ``build`` command
async def do_build(args: argparse.Namespace, client: aiohttp.ClientSession) -> int:
    incremental = args.incremental
    project = args.project
    assert isinstance(incremental, bool)
    assert isinstance(project, str)

    response = await client.get(
        f"/projects/{project}/start", params=dict(increment="1") if incremental else dict()
    )
    if response.status == 429:
        print("project already running", file=sys.stderr)
        return 1

    # Some unknown error.
    response.raise_for_status()
    return 1


do_build_parser = subcommands.add_parser("build", help="Start building a project.")
do_build_parser.add_argument(
    "--incremental",
    help="Whether to perform an incremental build (default: yes)",
    dest="incremental",
    action=argparse.BooleanOptionalAction,
    default=True,
)
do_build_parser.add_argument("project", help="Project slug")


def main() -> None:
    parsed = argparser.parse_args()
    if not parsed.command:
        argparser.print_help()
        exit(1)

    command: T.Callable[[argparse.Namespace, aiohttp.ClientSession], T.Coroutine[None, None, int]]
    if parsed.command == "build":
        command = do_build
    else:
        # mypy can't check this :(
        assert False, "didn't add a case for a command"

    async def run_command() -> None:
        base_url = parsed.coordinator_url
        assert base_url is None or isinstance(base_url, str)
        if base_url is None:
            coordinator_config = config.load_and_validate_config(
                "coordinator.toml", config.CoordinatorConfig
            )
            client_session = xbu_http.aiohttp_client_session_for_bind(coordinator_config.http_bind)
        else:
            client_session = aiohttp.ClientSession(base_url)

        async with client_session:
            code = await command(parsed, client_session)

        exit(code)

    asyncio.run(run_command())
