# SPDX-License-Identifier: AGPL-3.0-only
import re
from enum import Enum

import attr
import msgpack
import valideer as V

import xbbs.util as xutils


class JobStatus(Enum):
    WAITING = (1, "Waiting", "running")
    RUNNING = (2, "Scheduled", "running")  # kinda dumb
    WAITING_FOR_DONE = (3, "Finishing up", "running")
    FAILED = (4, "Failed", "failed", "failed")
    SUCCESS = (5, "Success", "success", "are successful")

    # special values
    PREREQUISITE_FAILED = (100, "Prerequisite failed", "failed",
                           "have failed prerequisites")
    UP_TO_DATE = (101, "Up to date", "success")
    IGNORED_FAILURE = (102, "Ignored failure", "failed",
                       "have failed silently")

    @property
    def pretty(self):
        return self.value[1]

    @property
    def kind(self):
        return self.value[2]

    @property
    def terminating(self):
        return self in [
            JobStatus.FAILED,
            JobStatus.SUCCESS,
            JobStatus.IGNORED_FAILURE,
            JobStatus.UP_TO_DATE
        ]

    @property
    def successful(self):
        return self in [
            JobStatus.SUCCESS,
            JobStatus.IGNORED_FAILURE,
            JobStatus.UP_TO_DATE
        ]

    @property
    def predicative(self):
        if len(self.value) > 3 and self.value[3]:
            return self.value[3]
        else:
            return f"are {self.pretty.lower()}"


class BuildState(Enum):
    SCHEDULED = (0, "Waiting to start...")
    FETCH = (1, "Fetching...")
    UPDATING_MIRRORS = (2, "Updating mirrors...")
    SETUP = (3, "Setting up...")
    CALCULATING = (4, "Calculating graph...")
    SETUP_REPOS = (5, "Setting up repositories...")
    RUNNING = (6, "Running...")
    DONE = (7, "Done")

    @property
    def pretty(self):
        return self.value[1]

    @property
    def terminating(self):
        return self in [
            BuildState.DONE
        ]


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


PKG_TOOL_VALIDATOR = V.parse(V.Mapping("string", {
    "version": "string",
    "architecture": V.AnyOf("string", V.AdaptBy(xutils.list_to_set)),
}))


@attr.s
class JobMessage(BaseMessage):
    @staticmethod
    def _filter(x, v):
        return v is not None
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
    prod_files = attr.ib()
    tool_repo = attr.ib()
    pkg_repo = attr.ib()
    commits_object = attr.ib(repr=False)
    # XXX: maybe it's worth doing something else
    xbps_keys = attr.ib(default=None, repr=False)
    mirror_root = attr.ib(default=None)
    distfile_path = attr.ib(default=None)
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
        "prod_files": ["string"],
        "tool_repo": "string",
        "pkg_repo": "string",
        "?distfile_path": "string",
        "?mirror_root": "string",
        "commits_object": V.Mapping("string", {
            "?rolling_id": "string",
            "?fixed_commit": "string"
        }),
        "?xbps_keys": V.Mapping(
            re.compile(r"^([a-zA-Z0-9]{2}:){15}[a-zA-Z0-9]{2}$"), bytes
        ),
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
        "artifact_type": V.Enum({"tool", "package", "file"}),
        "artifact": "string",
        # TODO(arsen): architecture
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
    pid = attr.ib()
    _validator = V.parse({
        "hostname": "string",
        "load": V.AdaptTo(tuple, ("number", "number", "number")),
        "pid": "integer",
        "projects": V.Mapping("string", {
            "git": "string",
            "description": "string",
            "classes": ["string"],
            "running": "boolean",
        })
    })


@attr.s
class BuildMessage(BaseMessage):
    project = attr.ib()
    delay = attr.ib()
    incremental = attr.ib(default=None)
    _validator = V.parse({
        "project": "string",
        "delay": "number",
        "?incremental": V.Nullable("boolean")
    })


@attr.s
class JobRequest(BaseMessage):
    capabilities = attr.ib()
    _validator = V.parse({
        "capabilities": V.AdaptBy(xutils.list_to_set)
    })


_thing.__exit__(None, None, None)
