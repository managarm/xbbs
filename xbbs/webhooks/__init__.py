# SPDX-License-Identifier: AGPL-3.0-only
import gevent.monkey # noqa isort:skip
if not gevent.monkey.is_module_patched("socket"): # noqa isort:skip
    gevent.monkey.patch_all() # noqa isort:skip
import hmac
import os
import os.path as path

import msgpack
import toml
import valideer as V
import zmq.green as zmq
from flask import Flask, request
from werkzeug.exceptions import (BadRequest, InternalServerError,
                                 ServiceUnavailable, Unauthorized)

app = Flask(__name__)
zctx = zmq.Context.instance()

with V.parsing(required_properties=True,
               additional_properties=V.Object.REMOVE):
    CONFIG_VALIDATOR = V.parse({
        "coordinator_endpoint": "string",
        "?coordinator_timeout": "integer",
        "?github_secret": "string",
        "?github": V.Mapping("string", "string")
    })

XBBS_CFG_DIR = os.getenv("XBBS_CFG_DIR", "/etc/xbbs")
with open(path.join(XBBS_CFG_DIR, "webhooks.toml"), "r") as fcfg:
    cfg = CONFIG_VALIDATOR.validate(toml.load(fcfg))

coordinator = cfg["coordinator_endpoint"]
cmd_timeout = cfg.get("coordinator_timeout", 1500)
hmac_key = cfg.get("github_secret", None)
github_mapping = cfg.get("github", {})


def verify_sig(data, secret, signature):
    s = hmac.new(secret.encode("utf-8"), data, digestmod="sha256")
    return hmac.compare_digest("sha256=" + s.hexdigest(), signature)


with V.parsing(required_properties=True,
               additional_properties=V.Object.REMOVE):
    GITHUB_PAYLOAD_VALIDATOR = V.parse({
        "repository": {
            "full_name": "string"
        }
    })


@app.route("/github-webhook", methods=["POST"])
def github():
    if hmac_key:
        sig = request.headers.get("X-Hub-Signature-256", None)
        if not sig:
            raise Unauthorized()
        if not verify_sig(request.data, hmac_key, sig):
            raise Unauthorized()

    if request.headers.get("X-GitHub-Event", None) != "push":
        return "", 204

    try:
        data = GITHUB_PAYLOAD_VALIDATOR.validate(request.json)
    except V.ValidationError:
        raise BadRequest()

    project = github_mapping.get(data["repository"]["full_name"], None)
    if not project:
        return "mapping not found", 404

    with zctx.socket(zmq.REQ) as conn:
        conn.set(zmq.LINGER, 0)
        conn.connect(coordinator)
        conn.send_multipart([b"build", msgpack.dumps(project)])
        if not conn.poll(cmd_timeout):
            raise ServiceUnavailable()
        (code, res) = conn.recv_multipart()

    if len(code) != 3:
        raise InternalServerError()
    code = int(code.decode("us-ascii"))
    if code == 204:
        return "success"
    res = msgpack.loads(res)
    return f"coordinator error: {code} {res}", 502
