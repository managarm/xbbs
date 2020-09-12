# SPDX-License-Identifier: AGPL-3.0-only
import attr
import msgpack
import valideer as V
import re


class BaseMessage:
    _filter = None

    def pack(self):
        return msgpack.dumps(attr.asdict(self, filter=self._filter))

    @classmethod
    def unpack(cls, data):
        x = msgpack.loads(data)
        # use adaption for nested data
        x = cls._validator.validate(x)
        val = cls(**x)
        val._dictvalue = x
        return val


_thing = V.parsing(required_properties=True, additional_properties=None)
_thing.__enter__()


@attr.s
class Heartbeat(BaseMessage):
    # status and statistics
    load = attr.ib()
    fqdn = attr.ib()
    # These are for display purposes on the web interface
    project = attr.ib(default=None)
    job = attr.ib(default=None)

    @staticmethod
    def _filter(x, v):
        return v is not None
    _validator = V.parse({
        "load": V.AdaptTo(tuple, ("number", "number", "number")),
        "fqdn": "string",
        "?project": "string",
        "?job": "string"
    })


@attr.s
class WorkMessage(BaseMessage):
    project = attr.ib()
    git = attr.ib()
    revision = attr.ib()
    _validator = V.parse({
        "project": "string",
        "git": "string",
        "revision": "string"
    })


PKG_TOOL_VALIDATOR = V.parse(V.Mapping("string", "string"))


@attr.s
class JobMessage(BaseMessage):
    project = attr.ib()
    job = attr.ib()
    repository = attr.ib()
    revision = attr.ib()
    output = attr.ib()
    build_root = attr.ib()
    needed_pkgs = attr.ib()
    needed_tools = attr.ib()
    prod_pkgs = attr.ib()
    prod_tools = attr.ib()
    tool_repo = attr.ib()
    pkg_repo = attr.ib()
    # XXX: maybe it's worth doing something else
    xbps_keys = attr.ib(default=None, repr=False)
    _validator = V.parse({
        "project": "string",
        "job": "string",
        "repository": "string",
        "revision": "string",
        "output": "string",
        "build_root": "string",
        "needed_pkgs": PKG_TOOL_VALIDATOR,
        "needed_tools": PKG_TOOL_VALIDATOR,
        "prod_pkgs": PKG_TOOL_VALIDATOR,
        "prod_tools": PKG_TOOL_VALIDATOR,
        "tool_repo": "string",
        "pkg_repo": "string",
        "?xbps_keys": V.Mapping(
            re.compile("^([a-zA-Z0-9]{2}:){15}[a-zA-Z0-9]{2}$"), bytes
        )
    })


def _is_blake2b_digest(x):
    # digest_size 64B for blake2b
    return isinstance(x, bytes) and len(x) == 64


@attr.s
class ArtifactMessage(BaseMessage):
    project = attr.ib()
    artifact_type = attr.ib()
    artifact = attr.ib()
    success = attr.ib()
    filename = attr.ib(default=None)
    last_hash = attr.ib(default=None)
    _validator = V.parse({
        "project": "string",
        "artifact_type": V.Enum({"tool", "package"}),
        "artifact": "string",
        "success": "boolean",
        "?filename": "?string",
        "?last_hash": V.Nullable(_is_blake2b_digest)
    })


@attr.s
class LogMessage(BaseMessage):
    project = attr.ib()
    job = attr.ib()
    line = attr.ib()
    _validator = V.parse({
        "project": "string",
        "job": "string",
        "line": "string"
    })


def _last_hash_validator(x):
    if x == b"initial":
        return True
    return _is_blake2b_digest(x)


@attr.s
class ChunkMessage(BaseMessage):
    last_hash = attr.ib()
    data = attr.ib()
    _validator = V.parse({
        "last_hash": _last_hash_validator,
        "data": bytes
    })


@attr.s
class JobCompletionMessage(BaseMessage):
    project = attr.ib()
    job = attr.ib()
    exit_code = attr.ib()
    run_time = attr.ib()
    _validator = V.parse({
        "project": "string",
        "job": "string",
        "exit_code": "number",
        "run_time": "number"
    })


@attr.s
class StatusMessage(BaseMessage):
    hostname = attr.ib()
    load = attr.ib()
    projects = attr.ib()
    _validator = V.parse({
        "hostname": "string",
        "load": V.AdaptTo(tuple, ("number", "number", "number")),
        "projects": V.Mapping("string", {
            "git": "string",
            "description": "string",
            "classes": ["string"],
            "running": "boolean"
        })
    })


_thing.__exit__(None, None, None)
