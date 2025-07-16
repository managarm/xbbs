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

import typing as T

from flask import request


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
