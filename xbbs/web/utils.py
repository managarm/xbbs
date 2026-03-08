# Small web utilities.
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
A few utilities for web-related tasks.
"""
import mimetypes
import typing as T

from flask import Response, request, send_from_directory
from werkzeug.security import safe_join

from xbbs.web.config import get_coordinator_work_root, get_nginx_xaccel_coord_root


def get_page_number() -> int:
    """Get a zero-indexed page number for this request."""
    try:
        return int(request.args.get("page", "0"))
    except ValueError:
        return 0


def get_page_size() -> int:
    """Get page size requested by the user."""
    try:
        limit = int(request.args.get("limit", "10"))
        if limit <= 0:
            return 10
        return limit
    except ValueError:
        return 10


Element = T.TypeVar("Element")


def extract_current_page(dataset: list[Element]) -> list[Element]:
    page = get_page_number()
    limit = get_page_size()
    return dataset[page * limit : (page + 1) * limit]


def send_from_coord_root_using_xaccel(directory: str, file: str) -> Response:
    """
    Try sending ``file`` in ``directory`` as if using Flask :func:`send_from_directory`, but using
    NGINX ``X-Accel-Redirect``, if possible.
    """
    # TODO(arsen): This is ugly and can definitely be done better.
    coord_root = get_coordinator_work_root()
    xaccel_coord_root = get_nginx_xaccel_coord_root()
    if not xaccel_coord_root:
        return send_from_directory(directory, file)

    real_path = safe_join(directory, file)
    if not real_path:
        # Let it raise the usual error.
        return send_from_directory(directory, file)

    # Check if this path is strictly in coord_root
    coord_root = coord_root.rstrip("/")
    if not real_path.startswith(f"{coord_root}/"):
        return send_from_directory(directory, file)

    xaccel_url = real_path.removeprefix(coord_root)

    mimetype, encoding = mimetypes.guess_type(real_path)
    if not mimetype:
        mimetype = "application/octet-stream"

    return Response(
        status=200,
        headers={"X-Accel-Redirect": xaccel_coord_root + xaccel_url},
        content_type=mimetype,
    )
