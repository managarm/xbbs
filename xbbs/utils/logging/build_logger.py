# Logger for build-related output, made to redirect into build logs.
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
This module contains a convenient logger used for logging build-specific logs.
"""

import traceback
import typing as T
from datetime import datetime, timezone

from . import LOG_TS_FORMAT


class BuildLogger:
    """
    A logger for formatting and printing data into coordinator and worker logs.  Usually, this
    information is not useful in syslog, and it has a better location to live in anyway.

    API is inspired by stdlib logging, but far less flexible.
    """

    def __init__(self, out_stream: T.TextIO) -> None:
        """
        Args:
          out_stream: Where to write the formatted output.
        """
        self.out_stream = out_stream
        """
        Where to write the formatted output.

        The user should also redirect uncaptured subprocess output here.
        """

    def _log(self, level: T.Literal["INFO", "ERROR"], message: str) -> None:
        now_ts = datetime.now(timezone.utc)
        print(
            f"[xbbs @ {now_ts.strftime(LOG_TS_FORMAT)} {level:>5}] {message}", file=self.out_stream
        )

    def info(self, message: str) -> None:
        """Logs an informative message."""
        self._log("INFO", message)

    def error(self, message: str) -> None:
        """Logs an error message."""
        self._log("ERROR", message)

    def exception(self, message: str) -> None:
        """Logs ``message`` as an error, accompanied by the current exception."""
        self._log("ERROR", message)
        traceback.print_exc(file=self.out_stream)
