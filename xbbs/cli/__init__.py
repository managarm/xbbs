# SPDX-License-Identifier: AGPL-3.0-only
import gevent.monkey; gevent.monkey.patch_all() # noqa isort:skip
import argparse
import msgpack
import os
import os.path as path
import sys
import toml
from xbbs.coordinator import CONFIG_VALIDATOR
import xbbs.messages as msgs
import zmq.green as zmq

zctx = zmq.Context.instance()
argparser = argparse.ArgumentParser(description="xbbs remote control cli")
subcommands = argparser.add_subparsers(
    dest="command"
)


def send_request(conn, cmd, arg):
    if type(arg) == str:
        arg = arg.encode("us-ascii")
    if type(cmd) == str:
        cmd = cmd.encode("us-ascii")
    conn.send_multipart([cmd, arg])
    if conn.poll(1500) == 0:
        raise RuntimeError("coordinator did not respond in time (1500ms)")
    (code, res) = conn.recv_multipart()
    if len(code) != 3:
        raise RuntimeError("coordinator returned invalid status value")
    code = int(code.decode("us-ascii"))
    return code, res


def do_status(conn, args):
    code, res = send_request(conn, "status", "")
    if code != 200:
        print("coordinator sent {msgpack.loads(res)}", file=sys.stderr)
        raise RuntimeError("returned response code was wrong: {code}")
    res = msgs.StatusMessage.unpack(res)
    print(f"hostname: {res.hostname}")
    print(f"load (1m, 5m, 15m): {res.load}")
    print("projects registered on coordinator:")
    for k, v in res.projects.items():
        print(f"* {k}:")
        print(f"    description: {v['description']}")
        print(f"    repository: {v['git']}")
        if v["running"]:
            print("    project is active")


do_status.parser = subcommands.add_parser(
    "status",
    help="print an overview of the coordinators status"
)


def do_build(conn, args):
    code, res = send_request(conn, "build", msgpack.dumps(args.project))
    if code == 204:
        return
    res = msgpack.loads(res)
    print(f"coordinator responded with {code} {res}", file=sys.stderr)
    exit(1)


do_build.parser = subcommands.add_parser(
    "build",
    help="start a project build"
)
do_build.parser.add_argument("project", help="project to build")


def main():
    XBBS_CFG_DIR = os.getenv("XBBS_CFG_DIR", "/etc/xbbs")
    with open(path.join(XBBS_CFG_DIR, "coordinator.toml"), "r") as fcfg:
        cfg = CONFIG_VALIDATOR.validate(toml.load(fcfg))

    parsed = argparser.parse_args()
    if not parsed.command:
        argparser.print_help()
        exit(1)

    if parsed.command == "status":
        subcommand = do_status
    elif parsed.command == "build":
        subcommand = do_build
    else:
        raise ValueError(f"unexpected command {parsed.command}")

    with zctx.socket(zmq.REQ) as conn:
        conn.set(zmq.LINGER, 0)
        conn.connect(cfg["command_endpoint"]["connect"])
        subcommand(conn, parsed)
