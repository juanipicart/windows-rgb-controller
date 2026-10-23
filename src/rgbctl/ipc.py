"""Local control channel between the CLI/tray and the daemon.

One JSON object per line over TCP on localhost:

    {"cmd": "status"}                 -> {"ok": true, "spec": {...}, "devices": [...]}
    {"cmd": "apply", "spec": {...}}   -> {"ok": true, "spec": {...}}  (merged into current)
    {"cmd": "quit"}                   -> {"ok": true}
"""

from __future__ import annotations

import json
import socket
from typing import Any

HOST = "127.0.0.1"
PORT = 6743


class DaemonNotRunning(Exception):
    pass


def request(message: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
            sock.sendall(json.dumps(message).encode() + b"\n")
            with sock.makefile("rb") as f:
                line = f.readline()
    except (ConnectionError, TimeoutError) as exc:  # refused, or reset mid-shutdown
        raise DaemonNotRunning from exc
    if not line:
        raise DaemonNotRunning("daemon closed the connection")
    reply = json.loads(line)
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "daemon error"))
    return reply


def is_running() -> bool:
    try:
        request({"cmd": "status"}, timeout=1.0)
        return True
    except DaemonNotRunning:
        return False
