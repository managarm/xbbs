# SPDX-License-Identifier: AGPL-3.0-only
import gevent.monkey; gevent.monkey.patch_all() # noqa isort:skip
import argparse
import os
import os.path as path
import sys

import msgpack
import toml
import zmq.green as zmq

import xbbs.messages as msgs
import xbbs.util as xutil
from xbbs.coordinator import CONFIG_VALIDATOR

zctx = zmq.Context.instance()
argparser = argparse.ArgumentParser(description="xbbs remote control cli")
subcommands = argparser.add_subparsers(
    dest="command"
)


def send_request(conn, cmd, arg):
    if isinstance(arg, str):
        arg = arg.encode("us-ascii")
    if isinstance(cmd, str):
        cmd = cmd.encode("us-ascii")
    conn.send_multipart([cmd, arg])
    # this should be configurable
    if not conn.poll(timeout=1500):
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
    code, res = send_request(conn, "build", msgs.BuildMessage(
        project=args.project,
        delay=0,
        incremental=args.incremental
    ).pack())
    if code == 204:
        return
    res = msgpack.loads(res)
    print(f"coordinator responded with {code} {res}", file=sys.stderr)
    exit(1)


do_build.parser = subcommands.add_parser(
    "build",
    help="start a project build"
)
do_build.parser.add_argument(
    "--incremental",
    help="whether to fully rebuild, when unspecified left as project default",
    dest="incremental",
    action=xutil.TristateBooleanAction
)
do_build.parser.add_argument("project", help="project to build")


def do_fail(conn, args):
    code, res = send_request(conn, "fail", msgpack.dumps(args.project))
    if code == 204:
        return
    res = msgpack.loads(res)
    print(f"coordinator responded with {code} {res}", file=sys.stderr)
    exit(1)


do_build.parser = subcommands.add_parser(
    "fail",
    help="fail all waiting jobs in a build"
)
do_build.parser.add_argument("project", help="project to fail")


def do_schedule(conn, args):
    code, res = send_request(conn, "build", msgs.BuildMessage(
        project=args.project,
        delay=args.delay,
        incremental=args.incremental
    ).pack())
    if code == 204:
        return
    res = msgpack.loads(res)
    print(f"coordinator responded with {code} {res}", file=sys.stderr)
    exit(1)


do_schedule.parser = subcommands.add_parser(
    "schedule",
    help="start a project build after some seconds"
)
do_schedule.parser.add_argument(
    "--incremental",
    help="whether to fully rebuild, when unspecified left as project default",
    dest="incremental",
    action=xutil.TristateBooleanAction
)
do_schedule.parser.add_argument("project", help="project to build")
do_schedule.parser.add_argument(
    "delay",
    help="delay, in seconds, to sleep",
    type=float
)


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
    elif parsed.command == "fail":
        subcommand = do_fail
    elif parsed.command == "schedule":
        subcommand = do_schedule
    else:
        raise ValueError(f"unexpected command {parsed.command}")

    with zctx.socket(zmq.REQ) as conn:
        conn.set(zmq.LINGER, 0)
        conn.connect(cfg["command_endpoint"]["connect"])
        subcommand(conn, parsed)
