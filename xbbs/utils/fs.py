# Filesystem utilities.
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
This package contains utilities used for dealing with the filesystem.
"""

import contextlib
import os
import os.path as path
import shutil
import tempfile
import typing as T

AnyPath: T.TypeAlias = os.PathLike[str] | str


def merge_tree_into(src: AnyPath, dst: AnyPath) -> None:
    """
    Copies, recursively, the file and directory structure ``src`` into ``dst``, merging
    the former tree into the latter.
    """

    def _raise(x: BaseException) -> None:
        raise x

    for root, dirs, files in os.walk(src, onerror=_raise):
        for dirn in dirs:
            source = path.join(root, dirn)
            dest = path.join(dst, path.relpath(root, start=src), dirn)
            try:
                os.mkdir(dest)
            except FileExistsError:
                pass
            shutil.copystat(source, dest)
        for filen in files:
            source = path.join(root, filen)
            dest = path.join(dst, path.relpath(root, start=src), filen)
            shutil.copy2(source, dest)


# TODO(arsen): match signature to open?
@contextlib.contextmanager
def atomic_write_open(fpath: str, mode: str) -> T.Generator[T.IO[T.Any]]:
    path_dir = path.dirname(fpath)
    with tempfile.NamedTemporaryFile(prefix=".", dir=path_dir, delete=False, mode=mode) as f:
        try:
            yield f
        except:  # noqa: E722
            os.unlink(f.name)
            raise
        os.rename(f.name, fpath)
