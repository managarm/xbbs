# String utilities.
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
This module contains various utility functions for dealing with strings.
"""


def fuse_with_slashes(first: str, *args: str) -> str:
    """
    Merges multiple strings such that there is exactly one slash between all of them.
    If the strings already contained slashes inbetween, they're collapsed down to one
    slash.
    """
    if len(args) == 0:
        return first

    first = first.rstrip("/")
    last = args[-1].lstrip("/")
    if len(args) == 1:
        return f"{first}/{last}"

    middle = "/".join(list(arg.strip("/") for arg in args[:-1]))
    return f"{first}/{middle}/{last}"
