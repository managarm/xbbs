xbbs
====
xbbs is a distributed dependency resolving build server designed to
interoperate with ``xbstrap`` to build operating system distributions.

xbbs consists of three modules:

- ``xbbs.coordinator``: generates a package graph and distributes it among
  workers. Workers are defined in ``coordinator.toml``. See the example in this
  repository.
- ``xbbs.worker``: receives commands from coordinator, runs them, reports logs
  and artifacts to the coordinator. All the workers share the same build root,
  determined by the coordinator. This is to ensure packages do not break when
  hardcoding absolute paths.
- ``xbbs.web``: read only web frontend to the coordinator providing access to
  logs, repositories, and build history

Additional documentation will be in ``Documentation/``.
