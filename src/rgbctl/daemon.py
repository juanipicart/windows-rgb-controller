"""Background service: keeps an effect running and accepts commands.

Threads:
- engine thread: builds a Session, runs the Engine, and rebuilds everything
  when devices fail, the PC resumes from sleep, or OpenRGB finishes
  detecting more devices (e.g. at boot, when we may start before it's done).
- command server: ipc.py protocol on localhost.
The caller's thread runs the tray icon (or just waits, with --no-tray).
"""

from __future__ import annotations

import json
import logging
import os
import socketserver
import threading
import time
from pathlib import Path
from typing import Any, Callable

from openrgb import OpenRGBClient

from rgbctl import config, ipc
from rgbctl.effects import DEFAULT_SPEC, from_spec, normalize_spec
from rgbctl.engine import Engine, StopReason
from rgbctl.session import Session

log = logging.getLogger(__name__)

RETRY_DELAY = 5.0
# How long after connecting we keep checking whether OpenRGB has detected
# more devices. Only matters right after boot; each check is a new SDK
# connection, so don't do it forever.
DETECTION_WATCH = 120.0
DETECTION_POLL = 10.0


def state_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "rgbctl" / "state.json"


def log_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "rgbctl" / "rgbctl.log"


class Daemon:
    def __init__(self, config_path: Path | None = None, fps: float = 30.0) -> None:
        self.config_path = config_path
        self.fps = fps
        self.spec = self._load_state()
        self.devices: list[str] = []
        self.on_quit: Callable[[], None] | None = None
        self._lock = threading.Lock()
        self._engine: Engine | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._server: socketserver.ThreadingTCPServer | None = None

    # -- state --------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        try:
            return normalize_spec(json.loads(state_path().read_text()))
        except FileNotFoundError:
            return dict(DEFAULT_SPEC)
        except (ValueError, OSError) as exc:
            log.warning("ignoring bad state file: %s", exc)
            return dict(DEFAULT_SPEC)

    def _save_state(self) -> None:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.spec, indent=2))

    def apply(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge changes into the current spec and switch to it."""
        with self._lock:
            spec = normalize_spec({**self.spec, **changes})
            effect = from_spec(spec)
            self.spec = spec
            self._save_state()
            if self._engine is not None:
                self._engine.update(effect=effect, brightness=spec["brightness"], sync=spec["sync"])
        log.info("applied %s", spec)
        return spec

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._server = _make_server(self)  # fails fast if another daemon owns the port
        for target, name in ((self._server.serve_forever, "ipc"), (self._engine_loop, "engine")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        log.info("daemon started, control port %d", ipc.PORT)

    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        log.info("shutting down")
        self._stop.set()
        with self._lock:
            if self._engine is not None:
                self._engine.stop()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=5.0)
        if self.on_quit is not None:
            self.on_quit()

    def wait(self) -> None:
        while not self._stop.wait(0.5):
            pass

    # -- engine supervision -------------------------------------------------

    def _engine_loop(self) -> None:
        while not self._stop.is_set():
            try:
                cfg = config.load(self.config_path)
                # Never launch OpenRGB ourselves: its Windows service owns that.
                session = Session(cfg, launch_openrgb=False)
            except Exception as exc:
                log.warning("could not open devices (%s); retrying in %.0fs", exc, RETRY_DELAY)
                self._stop.wait(RETRY_DELAY)
                continue

            self.devices = [d.name for d in session.devices]
            log.info("devices: %s", ", ".join(self.devices) or "none")
            with self._lock:
                engine = Engine(
                    session.devices,
                    fps=self.fps,
                    brightness=self.spec["brightness"],
                    sync=self.spec["sync"],
                    effect=from_spec(self.spec),
                )
                self._engine = engine
            watcher = threading.Thread(
                target=self._watch_detection, args=(cfg, session, engine), name="detect-watch", daemon=True
            )
            watcher.start()
            try:
                reason = engine.run()
            finally:
                with self._lock:
                    self._engine = None
                engine.stop()  # ends the watcher too
                session.close()

            if reason is StopReason.STOPPED and self._stop.is_set():
                return
            log.info("engine stopped (%s); reconnecting", reason.value)
            if reason is StopReason.UNHEALTHY:
                self._stop.wait(RETRY_DELAY)
            elif reason is StopReason.RESUMED:
                self._stop.wait(2.0)  # give USB devices a moment after wake

    def _watch_detection(self, cfg: config.Config, session: Session, engine: Engine) -> None:
        """Restart the engine if OpenRGB reports more devices than we have."""
        known = session.openrgb_device_count
        deadline = time.monotonic() + DETECTION_WATCH
        while time.monotonic() < deadline:
            if engine.wait_stopped(DETECTION_POLL):
                return
            try:
                client = OpenRGBClient(cfg.openrgb_host, cfg.openrgb_port, name="rgbctl-watch")
                count = len(client.devices)
                client.disconnect()
            except Exception:
                continue
            if count > known:
                log.info("OpenRGB now reports %d devices (had %d); reconnecting", count, known)
                engine.stop()
                return


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        line = self.rfile.readline()
        try:
            msg = json.loads(line)
            reply = _dispatch(self.server.rgb_daemon, msg)
        except Exception as exc:
            reply = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(reply).encode() + b"\n")


def _dispatch(daemon: Daemon, msg: dict[str, Any]) -> dict[str, Any]:
    match msg.get("cmd"):
        case "status":
            return {"ok": True, "spec": daemon.spec, "devices": daemon.devices}
        case "apply":
            return {"ok": True, "spec": daemon.apply(msg.get("spec", {}))}
        case "quit":
            threading.Thread(target=daemon.shutdown, daemon=True).start()
            return {"ok": True}
    raise ValueError(f"unknown command {msg.get('cmd')!r}")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows reuse would let two daemons bind

    def __init__(self, daemon: Daemon) -> None:
        self.rgb_daemon = daemon
        super().__init__((ipc.HOST, ipc.PORT), _Handler)


def _make_server(daemon: Daemon) -> _Server:
    try:
        return _Server(daemon)
    except OSError as exc:
        raise RuntimeError(f"rgbctl daemon already running (port {ipc.PORT} in use)") from exc
