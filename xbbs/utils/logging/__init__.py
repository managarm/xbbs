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
Helper functions for some logging-related tasks.
"""

import logging
import typing as T

if T.TYPE_CHECKING:
    from xbbs.data.config import LoggingConfig


LOG_TS_FORMAT = "%Y-%m-%d %H:%M:%S%z"
"""Format that timestamps will be logged in."""


def _get_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="[%(asctime)s %(levelname)7s%(xbbs_context)s] %(name)s: %(message)s",
        datefmt=LOG_TS_FORMAT,
    )


class ContextFormattingFilter(logging.Filter):
    """
    A filter that formats our contextual information.  For now, those are the
    ``project``, ``build_id``, ``node`` and ``execution_id`` strings.
    """

    _CONTEXT_FIELDS = ("project", "build_id", "node", "execution_id")

    def filter(self, record: logging.LogRecord) -> bool:
        record.xbbs_context = "/".join(
            getattr(record, field) for field in self._CONTEXT_FIELDS if hasattr(record, field)
        )
        for field in self._CONTEXT_FIELDS:
            try:
                delattr(record, field)
            except AttributeError:
                pass
        if record.xbbs_context:  # type: ignore[attr-defined]
            record.xbbs_context = f" {record.xbbs_context}"  # type: ignore[attr-defined]
        return True


def create_stream_handler(stream: T.TextIO | None = None) -> logging.StreamHandler[T.TextIO]:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_get_formatter())
    handler.addFilter(ContextFormattingFilter())
    return handler


def apply_logging_config(config: "LoggingConfig") -> None:
    """
    Given a :py:class:`LoggingConfig`, apply the logging configuration to stdlib
    :py:mod:`logging`.
    """

    # Tell `logging` about us.
    handler = create_stream_handler()
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if config.debug else logging.INFO)
