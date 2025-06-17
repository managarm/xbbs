# Common argument parsing code.
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
This module contains a few utilities used with :py:module:`argparse` in a few
places.
"""

import argparse


def create_root_parser(description: str) -> argparse.ArgumentParser:
    """
    Creates a root :py:class:`argparse.ArgumentParser` with some standard flags
    and configuration.
    """
    from xbbs import __version__

    parser = argparse.ArgumentParser(
        description=description, epilog="Report bugs at <https://github.com/managarm/xbbs/new/>."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"""\
xbbs v{__version__}

Copyright (C) 2025 Arsen Arsenović <arsen@managarm.org>
License AGPLv3+: GNU AGPL version 3 or later <https://gnu.org/licenses/agpl.html>.
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.
""",
    )

    return parser
