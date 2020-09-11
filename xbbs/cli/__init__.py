# SPDX-License-Identifier: AGPL-3.0-only
import gevent.monkey
gevent.monkey.patch_all()
import argparse
import zmq.green as zmq
import os
import os.path as path
import toml
from xbbs.coordinator import CONFIG_VALIDATOR
import xbbs.messages as msgs

zctx = zmq.Context.instance()


def send_request(cmd, arg):
    if type(arg) == str:
        arg = arg.encode("us-ascii")
    if type(cmd) == str:
        cmd = cmd.encode("us-ascii")
    coordinator_socket.send_multipart([cmd, arg])
    if coordinator_socket.poll(1500) == 0:
        raise RuntimeError("coordinator did not respond in time (1500ms)")
    return coordinator_socket.recv_multipart()


def main():
    global coordinator_socket
    XBBS_CFG_DIR = os.getenv("XBBS_CFG_DIR", "/etc/xbbs")
    with open(path.join(XBBS_CFG_DIR, "coordinator.toml"), "r") as fcfg:
        cfg = CONFIG_VALIDATOR.validate(toml.load(fcfg))

    with zctx.socket(zmq.REQ) as coordinator_socket:
        coordinator_socket.set(zmq.LINGER, 0)
        coordinator_socket.connect(cfg["command_endpoint"]["connect"])
        code, res = send_request("status", "")
        res = msgs.StatusMessage.unpack(res)
        print(f"on host {res.hostname}")
        print(f"load  (1m, 5m, 15m): {res.load}")
        print("projects registered on coordinator:")
        for k, v in res.projects.items():
            print(f"* {k}:")
            print(f"    description: {v['description']}")
            print(f"    repository: {v['git']}")
            if v["running"]:
                print(f"    project is active")
