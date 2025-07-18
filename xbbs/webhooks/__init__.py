# Start xbbs builds when receiving a webhook event
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

import hmac
from urllib.parse import urljoin

import requests
from flask import Blueprint, Flask, g, request
from werkzeug.exceptions import BadRequest, Unauthorized

from xbbs.data.config import XbbsWebhooksConfig, load_and_validate_config

_bp = Blueprint("root", __name__)


def verify_gh_sig(data: bytes, secret: str, signature: str) -> bool:
    """
    Given request data ``data``, and a expected secret ``secret``, returns ``True`` iff
    ``signature`` was produced with the same secret.

    The ``signature`` is delivered via the ``X-Hub-Signature-256`` header by GitHub.
    """
    s = hmac.new(secret.encode("utf-8"), data, digestmod="sha256")
    return hmac.compare_digest("sha256=" + s.hexdigest(), signature)


@_bp.post("/github-webhook")
def github() -> tuple[str, int]:
    config: XbbsWebhooksConfig = g.config

    # First, authorization.
    if config.github_secret:
        signature = request.headers.get("X-Hub-Signature-256", None)
        if not signature:
            raise Unauthorized()
        if not verify_gh_sig(request.data, config.github_secret, signature):
            raise Unauthorized()

    if request.headers.get("X-Github-Event", "") != "push":
        # We only care about pushes.
        return "", 204

    # Parse out the full_name.
    body_json = request.json
    if not isinstance(body_json, dict):
        raise BadRequest()
    repository = body_json.get("repository", None)
    if not isinstance(repository, dict):
        raise BadRequest()
    full_name = repository.get("full_name", None)
    if not isinstance(full_name, str):
        raise BadRequest()

    project_slugs = config.github_repo_to_projects.get(full_name, None)
    if project_slugs is None:
        # Not considered an error, really.  It just means that the event was irrelevant.
        return "mapping not found", 200

    for project in project_slugs:
        requests.get(
            urljoin(config.coordinator_url, f"/projects/{project}/start"),
            params=dict(delay=config.start_delay, increment=1),
        )

    return "starting", 200


def load_webhooks_config() -> None:
    """Loads the ``xbbs-webhook`` configuration into ``g.config``."""
    g.config = load_and_validate_config("webhooks.toml", XbbsWebhooksConfig)


def create_app() -> Flask:
    """
    Create a WSGI application for ``xbbs.webhooks``.
    """
    app = Flask(__name__)
    app.register_blueprint(_bp)

    app.before_request(load_webhooks_config)

    return app
