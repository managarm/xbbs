import attr
import valideer as V
import msgpack
import msgpack


class BaseMessage:
    _filter = None
    def pack(self):
        return msgpack.dumps(attr.asdict(self, filter=self._filter))

    @classmethod
    def unpack(cls, data):
        x = msgpack.loads(data)
        # use adaption for nested data
        x = cls._validator.validate(x)
        return cls(**x)

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
    _validator = V.parse({
        "project": "string",
        "job": "string",
        "repository": "string",
        "revision": "string",
        "output": "string",
        "build_root": "string",
        "needed_pkgs": ["string"],
        "needed_tools": ["string"],
        "prod_pkgs": ["string"],
        "prod_tools": ["string"],
        "tool_repo": "string",
        "pkg_repo": "string",
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

_thing.__exit__(None, None, None)
