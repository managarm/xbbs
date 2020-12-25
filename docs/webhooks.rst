xbbs.webhooks
=============
``xbbs.webhooks`` was written to work with gevent-powered gunicorn, and uses
``zmq.green`` APIs.

Configuration is store in a file at ``${XBBS_CFG_DIR}/webhooks.toml`` and
contains the following:

.. code-block:: toml

    # Where the xbbs coordinator exposes the command socket
    coordinator_endpoint = "ipc:///tmp/xbbs-cmd-socket"
    # Secret given to GitHub for SHA256 HMAC
    github_secret = "secret"

    [github]
    # A section containing `fullname' mappings to xbbs project names
    "ArsenArsen/testrepo" = "managarm"
    # ...

An example command line for gunicorn:
-------------------------------------
.. code-block:: sh

    gunicorn -k gevent -b 127.0.0.1:8080 xbbs.web:app
