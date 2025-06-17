# Configuration file models
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
Data models and validation schemas for configuration files.
"""

import logging
import os
import os.path as path
import sys
import typing as T

import toml
from pydantic import BaseModel, Field, ValidationError

from xbbs.buildsystem import BuildSystem

logger = logging.getLogger(__name__)


class LoggingConfig(BaseModel):
    """
    Common logging configuration.
    """

    debug: bool = Field(default=False)
    """
    If ``true``, enables logging the debug level.  This can be extremely verbose.
    """


class WorkerConfig(BaseModel):
    """
    Configuration model for the worker.
    """

    log: LoggingConfig = Field(default_factory=LoggingConfig)
    """
    Logger configuration.  See :py:class:`LoggingConfig`.
    """

    coordinator_url: str
    """
    URL on which the coordinator internal API is exposed.
    """

    capabilities: set[str] = Field(default_factory=set)
    """
    Capabilities exposed by this worker.  Defaults to no special capabilities.

    Capabilities are a set of strings defined by convention per project that a build
    system may attach to tasks to inform the scheduler which workers are capable of
    processing a task.  A task may only be scheduled on a worker if it possesses all the
    capabilities the task asks for.
    """

    work_root: str
    """
    The location where the worker will keep the temporary build directories.
    """


class CoordinatorConfig(BaseModel):
    """
    Configuration model for the coordinator.
    """

    log: LoggingConfig = Field(default_factory=LoggingConfig)
    """
    Logger configuration.  See :py:class:`LoggingConfig`.
    """

    http_bind: T.Union[str, T.Annotated[int, Field(ge=1, le=65535)]]
    """
    If a string, represents a path for a UNIX socket on which to bind the
    coordinators HTTP interface.  If an integer, represents port to us for that
    HTTP interface.  As this is a trusted interface, binding always happens on
    localhost.
    """

    work_root: str
    """
    The location where the coordinator will keep artifacts, jobs, projects and
    mirrors.
    """

    projects: dict[T.Annotated[str, Field(pattern="^[a-zA-Z0-9_]+$")], "Project"]
    """
    Projects this build server takes care of.  Maps project "slugs" to project
    metadata.  Slugs are used as identifiers in commands and internal comms.
    """

    def get_path_host_port(self) -> T.Union[
        T.Tuple[str, None, None],
        T.Tuple[None, str, int],
    ]:
        if isinstance(self.http_bind, str):
            return (self.http_bind, None, None)
        else:
            return (None, "127.0.0.1", self.http_bind)


class XbstrapSigningKeyData(BaseModel):
    """
    Specifies a keypair xbbs will use to sign and verify packages built by ``xbstrap``.

    Such keypairs can be generated using the ``xbps-keygen`` script provided as part of
    the xbbs source tree.
    """

    fingerprint: str
    """
    Fingerprint of the key pair used to sign and verify packages.
    """

    public_key: str
    """
    Absolute path to the public part of this keypair.  Generated as a ``.plist`` file by
    ``xbps-keygen``.
    """

    private_key: str
    """
    Absolute path to the private part of this keypair.  Generated as a ``.rsa`` file by
    ``xbps-keygen``.
    """


class XbstrapMirrorConfig(BaseModel):
    """Configuration for ``xbstrap-mirror``.  Allows the coordinator to provide mirrors of
    source repositories the ``xbstrap`` distro uses.  A good bit of courtesy we should
    all strive to do.

    This feature is not enabled by default, as it requires extra setup outside of the
    coordinator.  The coordinator can't actually serve Git repositories.  If one wishes
    to use this feature (highly recommended), they will need to do some extra setup.

    The coordinator will run ``xbstrap-mirror`` in order to update a *mirror build*.
    The mirror build is a special build directory containing only directories in format
    ``mirrors/${vcs}/${source_name}`` that contain mirrors of upstream source
    repositories.

    Currently, ``xbstrap`` can only mirror Git repositories, so ``vcs`` is usually
    ``git``.

    The administrator should expose this ``mirrors/`` directory structure via
    appropriate VCS-specific tools on some endpoint accessible by all workers.  This
    endpoint is called the *mirror base URL*.  This requires extra configuration; the
    following is an example snippet of nginx_ configuration that exposes a mirror build
    at ``/var/lib/xbbs/mirror_build`` at base URL
    ``https://mirrors.managarm.org/mirror``::

        server {
                listen 443 ssl http2;
                server_name mirrors.managarm.org;

                location /mirror/git/ {
                        rewrite  ^/mirror/git/(.*) /$1 break;
                        fastcgi_pass  unix:/run/fcgiwrap.socket;
                        include       fastcgi_params;
                        fastcgi_param SCRIPT_FILENAME     /usr/lib/git-core/git-http-backend;
                        fastcgi_param GIT_HTTP_EXPORT_ALL "yes";
                        fastcgi_param GIT_PROJECT_ROOT    /var/lib/xbbs/mirror_build/mirror/git;
                        fastcgi_param PATH_INFO           $uri;
                }
        }

    The mirror build directory can be shared between multiple projects, if they share
    the same sources.  The coordinator will arrange for these directories to be updated
    at the start of the build.

    ``xbbs`` will arrange the provided mirror base URL (in the example above,
    ``https://mirrors.managarm.org/mirror``) to be used for fetching mirrored sources on
    each worker.

    .. _nginx: https://nginx.org/

    """

    build_directory: str
    """Path to the mirror build directory."""

    base_url: str
    """Base URL on which the mirror directory structure is exposed."""


class XbstrapProjectConfig(BaseModel):
    """
    ``xbstrap``-specific project configuration.
    """

    type: T.Literal["xbstrap"]
    """
    Build system type.
    """

    distfile_path: str | None = Field(default="xbbs/")
    """
    In-repository relative path that contains files to copy into each build
    tree.  Useful for providing build system configuration.
    """

    signing_key: XbstrapSigningKeyData | None = Field(default=None)
    """
    Configuration for the signing keys used for this build.  If missing, packages will
    be left unsigned.  Such a configuration is not very useful, as ``xbps-install`` will
    refuse to install packages on the worker without signatures.
    """

    mirror: XbstrapMirrorConfig | None = Field(default=None)
    """
    If present, enables mirroring support in xbstrap on the coordinator and on the
    workers.  See documentation for :py:class:`XbstrapMirrorConfig` for more
    information.
    """

    def type_enum(self) -> BuildSystem:
        """Get an enum version of the build system type."""
        return BuildSystem.XBSTRAP


class Project(BaseModel):
    """
    Data about a specific project.
    """

    name: str
    """
    User-facing project name.
    """

    description: str
    """
    Brief project description
    """

    classes: set[str] = Field(default_factory=set)
    """
    Extra CSS classes for use in the web UI.
    """

    git: str
    """
    URL to clone to obtain sources.
    """

    buildsystem: XbstrapProjectConfig = Field(discriminator="type")
    """
    Build system and matching configuration to use in this project.  The type and
    available configuration is picked based on the ``type`` field of this object.
    """


class XbbsWebhooksConfig(BaseModel):
    """
    Configuration file for ``xbbs-webhooks``.
    """

    coordinator_url: str
    """
    URL on which the coordinator internal API is exposed.
    """

    start_delay: int = Field(default=0)
    """
    Number of seconds to delay starting the build.  Useful for debouncing.  Defaults to zero
    (immediate start).
    """

    github_secret: str | None = Field(default=None)
    """
    Secret GitHub uses to sign payloads delivered over the webhook endpoint.  See `GitHub Docs`_
    for more information on this feature.

    Defaults to disabled (i.e. no validation).  It is highly recommended to enable this feature.

    .. _`GitHub Docs`:
       https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
    """

    github_repo_to_projects: dict[str, list[str]] = Field(default=dict())
    """
    Mapping from GitHub repository names to project slugs.  Whenever a ``push`` event for a
    repository is received, all the slugs associated with that repositories ``full_name`` will be
    started, with the specified delay.
    """


def load_and_validate_config[M: BaseModel](config_file: str, model: type[M]) -> M:
    """
    Validate and load a config file as the given model.

    Args:
      config_file: Filename to open in the config directory
      model: A Pydantic model by which to validate the loaded config

    Returns:
      A parsed config.

    Raises:
      SystemExit: if configuration parsing fails.  Exit code 1.
    """

    config_dir = os.getenv("XBBS_CFG_DIR") or "/etc/xbbs"

    try:
        with open(path.join(config_dir, config_file), "r") as config:
            return model.model_validate(toml.load(config))
    except (ValidationError, toml.TomlDecodeError):
        # TODO(arsen): format error nicely
        logger.exception("failed to parse config")
        sys.exit(1)
    except Exception:
        logger.exception("failed to load config")
        sys.exit(1)
