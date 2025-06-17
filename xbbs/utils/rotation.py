# Helper script for rotating old builds.
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
This module contains a helper for rotating builds.  When ran, it will remove builds older than 14
days, keeping at least one successful build, and the latest build.
"""

# TODO(arsen): this should be more flexible

import datetime as dt
import os
import os.path as path
import shutil
import typing as T

import xbbs.coordinator.build_state as xbc_bs
import xbbs.data.config as config
import xbbs.utils.argparse as xbu_cli
import xbbs.utils.ts as xbu_ts


def rotate_project_by_directory(project_dir: str, max_age: dt.timedelta) -> list[str]:
    """
    Given a project directory ``project_dir``, determine which builds should be removed.

    Returns:
      A list of builds that should be removed.
    """
    builds = [
        (ts, build)
        for build in os.listdir(project_dir)
        if (ts := xbu_ts.parse_build_id_into_ts(build))
    ]
    builds.sort()

    found_success = False
    builds_to_remove = []
    for ts, build in reversed(builds):
        build_conn = xbc_bs.open_build_db(path.join(project_dir, build))
        try:
            build_info = xbc_bs.read_build_object(build_conn)
        finally:
            build_conn.close()

        if not build_info.state.is_final:
            # We aren't interested in builds that haven't concluded yet
            continue

        if not found_success and build_info.state.is_success:
            # We don't want to remove the latest successful build.
            found_success = True
            continue

        # If this build is the latest, skip it.  We do this after the success check so that a
        # successful latest build would be counted as both a latest successful and latest.
        if build == builds[-1][1]:
            continue

        # If older than the maximum age, delete it.
        if dt.datetime.now(dt.timezone.utc) - ts > max_age:
            builds_to_remove.append(build)

    return builds_to_remove


def rotate_projects(
    work_root: str,
    project_filter: T.Callable[[str], bool],
    dry_run: bool,
) -> None:
    """
    In the given ``work_root``, iterate all known projects and rotate their
    builds if they match ``project_filter``.
    """
    projects_dir = path.join(work_root, "projects")
    dirs_to_remove = []
    for project in os.listdir(projects_dir):
        if not project_filter(project):
            continue

        to_remove = rotate_project_by_directory(
            path.join(projects_dir, project), dt.timedelta(days=14)
        )

        for build in to_remove:
            dirs_to_remove.append(path.join(projects_dir, project, build))

    if dry_run:
        print("Would remove:")
        for dir in dirs_to_remove:
            print(f"- {dir}")
    else:
        for dir in dirs_to_remove:
            # TODO(arsen): nicer error handling
            shutil.rmtree(dir)


def main() -> None:
    argparser = xbu_cli.create_root_parser(
        """
        Discard builds older than 14 days, keeping at least one successful build, and the latest
        build.
        """
    )
    argparser.add_argument(
        "--coordinator-work-root",
        help="""
        coordinator work_root directory (default: read from config)
        """,
        metavar="WORK_ROOT",
    )
    argparser.add_argument(
        "--projects",
        action="append",
        help="""
        limit processing to some projects (may be specified multiple times, default: all projects)
        """,
        metavar="PROJECT_SLUG",
        default=[],
    )
    argparser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="do not actually delete any files",
        default=False,
    )

    opts = argparser.parse_args()

    work_root = opts.coordinator_work_root
    if work_root is None:
        coordinator_config = config.load_and_validate_config(
            "coordinator.toml", config.CoordinatorConfig
        )
        work_root = coordinator_config.work_root
    assert isinstance(work_root, str)
    projects = T.cast(list[str], opts.projects)

    if len(projects) == 0:

        def project_filter(project_name: str) -> bool:
            return True

    else:

        def project_filter(project_name: str) -> bool:
            return project_name in projects

    rotate_projects(
        work_root=work_root,
        project_filter=project_filter,
        dry_run=opts.dry_run,
    )
