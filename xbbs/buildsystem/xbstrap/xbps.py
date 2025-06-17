# Routines for dealing with xbps repositories.
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
This module contains helpers for dealing with xbps repositories.
"""

import plistlib
import tarfile
import typing as T

import zstandard


class XbpsRepodataEntry(T.TypedDict):
    """
    An xbps repodata file.  Not complete - unused fields are missing.
    """

    pkgver: str


XbpsRepodata: T.TypeAlias = dict[str, XbpsRepodataEntry]
"""A repodata files contents."""


def read_xbps_repodata(repodata_file: str) -> XbpsRepodata:
    """
    Parses a presumed-correct repodata file.
    """
    with open(repodata_file, "rb") as zidx:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(zidx) as reader, tarfile.open(fileobj=reader, mode="r|") as t:
            for x in t:
                if x.name != "index.plist":
                    continue
                idxpl = t.extractfile(x)
                if not idxpl:
                    continue
                with idxpl:
                    pkg_idx = plistlib.load(idxpl, fmt=plistlib.FMT_XML)
                    return T.cast(XbpsRepodata, pkg_idx)

        raise RuntimeError("no index.plist?  not possible")
