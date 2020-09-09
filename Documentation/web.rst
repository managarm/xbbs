xbbs.web
========
``xbbs.web`` was written to work with gevent-powered gunicorn, and uses
``zmq.green`` APIs.

``xbbs.web`` takes settings using env variables:

- ``XBBS_COORDINATOR_ENDPOINT``: the endpoint on which the xbbs coordinator is
  listening for commands on
- ``XBBS_PROJECT_BASE``: the base directory where the coordinator stores
  projects. Used for finding log files, builds and repositories.
- ``XBBS_USE_X_SENDFILE`` (optional, default="False"): use X-Sendfile header
  instead of having Flask handle sending a file from disk. Used for log files
  and repositories. Recommended if your webserver supports it. Truthy values,
  case ignored: "1", "t", "true", "yes"

An example command line for gunicorn:
-------------------------------------
.. code-block:: sh
   :linenos:

    XBBS_USE_X_SENDFILE=true \
    XBBS_PROJECT_BASE=/var/xbbs/projects \
    XBBS_COORDINATOR_ENDPOINT=tcp://localhost:1600 \
    gunicorn -k gevent -b 127.0.0.1:8080 xbbs.web:app
