from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rgbctl import config, ipc
from rgbctl.color import Color
from rgbctl.effects import EFFECT_NAMES, from_spec, normalize_spec
from rgbctl.engine import Engine
from rgbctl.session import Session

STARTUP_SHORTCUT = (
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "rgbctl.lnk"
)


def _session(args: argparse.Namespace) -> Session:
    return Session(config.load(args.config), port=args.port, launch_openrgb=not args.no_launch)


def _send_to_daemon(spec: dict[str, Any]) -> bool:
    """Apply spec via the running daemon; False if there isn't one."""
    try:
        reply = ipc.request({"cmd": "apply", "spec": spec})
    except ipc.DaemonNotRunning:
        return False
    applied = reply["spec"]
    print(f"daemon: {applied['name']} {applied['color'] if applied['name'] not in ('rainbow', 'wave', 'fire', 'off') else ''}".rstrip())
    return True


# -- one-shot commands --------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    try:
        status = ipc.request({"cmd": "status"})
    except ipc.DaemonNotRunning:
        pass
    else:
        # The daemon owns the devices; ask it instead of opening them twice.
        for i, name in enumerate(status["devices"]):
            print(f"[{i}] {name}")
        return 0

    session = _session(args)
    try:
        if not session.devices:
            print("No devices found.")
            return 1
        for i, dev in enumerate(session.devices):
            note = "  <- no LEDs sized, configure zones" if dev.led_count == 0 else ""
            print(f"[{i}] {dev.name:<40} {dev.kind:<12} {dev.led_count:>4} LEDs  ({dev.backend}){note}")
        return 0
    finally:
        session.close()


def cmd_set(args: argparse.Namespace) -> int:
    color = Color.parse(args.color)
    spec = {"name": "off"} if args.color.lower() in ("off", "black") else {"name": "static", "color": args.color}
    if _send_to_daemon(spec):
        return 0

    session = _session(args)
    try:
        failed = 0
        for dev in session.devices:
            if dev.led_count == 0:
                print(f"  {dev.name}: skipped (no LEDs sized)")
                continue
            try:
                dev.fill(color)
                print(f"  {dev.name}: {color}")
            except Exception as exc:  # one bad device shouldn't stop the rest
                failed += 1
                print(f"  {dev.name}: FAILED ({exc})", file=sys.stderr)
        return 1 if failed else 0
    finally:
        session.close()


def cmd_effect(args: argparse.Namespace) -> int:
    changes: dict[str, Any] = {
        "name": args.name,
        "color": args.color or "purple",
        "period": args.period,
        "spread": args.spread,
        "sync": args.sync,
        "brightness": args.brightness,
    }
    # Only send speed when given, so the daemon keeps the one set in the UI.
    if args.speed is not None:
        changes["speed"] = args.speed
    spec = normalize_spec(changes)
    if args.duration is None and _send_to_daemon(changes):
        return 0

    session = _session(args)
    engine = Engine(
        session.devices, fps=args.fps, brightness=spec["brightness"], sync=spec["sync"], speed=spec["speed"]
    )
    print(f"Running {args.name} on {len(engine.devices)} devices at {args.fps:g} fps (Ctrl+C to stop)")
    try:
        engine.run(from_spec(spec), duration=args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


# -- daemon management --------------------------------------------------------


def _setup_daemon_logging() -> None:
    from rgbctl.daemon import log_path

    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # None under pythonw.exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True
    )


def cmd_daemon(args: argparse.Namespace) -> int:
    from rgbctl.daemon import Daemon

    _setup_daemon_logging()
    daemon = Daemon(config_path=args.config)
    try:
        daemon.start()
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1
    try:
        if args.no_tray:
            daemon.wait()
        else:
            from rgbctl.tray import run_tray

            run_tray(daemon)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.shutdown()
    return 0


def _daemon_command(args: argparse.Namespace) -> list[str]:
    """Command line that starts the daemon without a console window."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    cfg = config.load(args.config)
    cmd = [str(pythonw), "-m", "rgbctl"]
    if cfg.path is not None:
        cmd += ["--config", str(cfg.path.resolve())]
    return cmd + ["daemon"]


def cmd_start(args: argparse.Namespace) -> int:
    if ipc.is_running():
        print("Daemon already running.")
        return 0
    subprocess.Popen(
        _daemon_command(args),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if ipc.is_running():
            print("Daemon started.")
            return 0
        time.sleep(0.3)
    from rgbctl.daemon import log_path

    print(f"Daemon did not come up; see {log_path()}", file=sys.stderr)
    return 1


def cmd_stop(args: argparse.Namespace) -> int:
    try:
        ipc.request({"cmd": "quit"})
    except ipc.DaemonNotRunning:
        print("Daemon not running.")
        return 0
    deadline = time.monotonic() + 10
    while ipc.is_running() and time.monotonic() < deadline:
        time.sleep(0.2)
    print("Daemon stopped.")
    return 0


def cmd_device(args: argparse.Namespace) -> int:
    try:
        status = ipc.request({"cmd": "status"})
    except ipc.DaemonNotRunning:
        print("Daemon not running; start it with `rgbctl start`.", file=sys.stderr)
        return 1
    devices, disabled = status["devices"], set(status["spec"]["disabled"])
    if args.action == "list":
        for key in devices:
            print(f"  {'off' if key in disabled else 'on ':<4} {key}")
        return 0
    # Exact key, else a unique case-insensitive substring (e.g. "g502").
    wanted = args.device.lower()
    found = [k for k in devices if k.lower() == wanted] or [k for k in devices if wanted in k.lower()]
    if len(found) != 1:
        problem = "matches several devices" if found else "matches no device"
        print(f"{args.device!r} {problem}: {', '.join(found or devices)}", file=sys.stderr)
        return 1
    key = found[0]
    disabled = disabled - {key} if args.action == "on" else disabled | {key}
    ipc.request({"cmd": "apply", "spec": {"disabled": sorted(disabled)}})
    print(f"{key}: {args.action}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        status = ipc.request({"cmd": "status"})
    except ipc.DaemonNotRunning:
        print("Daemon not running.")
        return 1
    spec = status["spec"]
    print(f"effect:     {spec['name']}" + (" (synced)" if spec["name"] == "wave" and spec["sync"] else ""))
    print(f"color:      {spec['color']}")
    print(f"speed:      {spec['speed']:g}x")
    print(f"brightness: {spec['brightness']:.0%}")
    off = set(spec["disabled"])
    names = [f"{key} (off)" if key in off else key for key in status["devices"]]
    print(f"devices:    {', '.join(names) or 'none (connecting...)'}")
    return 0


def cmd_install_startup(args: argparse.Namespace) -> int:
    cmd = _daemon_command(args)
    target, arguments = cmd[0], subprocess.list2cmdline(cmd[1:])
    workdir = str(Path(target).parent)
    # WScript.Shell is the standard way to create a .lnk without extra packages.
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath = $env:TARGET; $s.Arguments = $env:ARGS; $s.WorkingDirectory = $env:WORKDIR;"
        "$s.Description = 'rgbctl lighting daemon'; $s.Save()"
    )
    env = {**os.environ, "LNK": str(STARTUP_SHORTCUT), "TARGET": target, "ARGS": arguments, "WORKDIR": workdir}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True)
    print(f"Installed: {STARTUP_SHORTCUT}")
    return 0


def cmd_uninstall_startup(args: argparse.Namespace) -> int:
    try:
        STARTUP_SHORTCUT.unlink()
        print(f"Removed: {STARTUP_SHORTCUT}")
    except FileNotFoundError:
        print("Not installed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rgbctl", description="Control your PC's RGB lighting.")
    parser.add_argument("--config", type=Path, help="config file (default: ./rgbctl.toml)")
    parser.add_argument("--port", type=int, help="OpenRGB SDK server port (overrides config)")
    parser.add_argument("--no-launch", action="store_true", help="don't auto-start OpenRGB")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list detected devices").set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="set every device to one color ('off' turns lights off)")
    p_set.add_argument("color", help="name (red), hex (#ff0000), r,g,b or off")
    p_set.set_defaults(func=cmd_set)

    p_fx = sub.add_parser("effect", help="run an animated effect (via the daemon if it's running)")
    p_fx.add_argument("name", choices=[n for n in EFFECT_NAMES if n != "off"])
    p_fx.add_argument("color", nargs="?", help="color (ignored by rainbow, wave and fire)")
    p_fx.add_argument("--speed", type=float, help="multiplier on the effect's default speed (0.25-4)")
    p_fx.add_argument("--period", type=float, help="seconds per cycle at 1x speed")
    p_fx.add_argument("--spread", type=float, default=1.0, help="rainbows/bands across the strip (wave, color_wave)")
    p_fx.add_argument("--sync", action="store_true", help="every device shows the same pattern")
    p_fx.add_argument("--fps", type=float, default=30.0)
    p_fx.add_argument("--brightness", type=float, default=1.0, help="0.0-1.0")
    p_fx.add_argument("--duration", type=float, help="run directly for N seconds instead of via the daemon")
    p_fx.set_defaults(func=cmd_effect)

    p_dev = sub.add_parser("device", help="turn one device's lighting off or back on (via the daemon)")
    p_dev.add_argument("action", choices=["on", "off", "list"])
    p_dev.add_argument("device", nargs="?", default="", help="device name, or a unique part of it (e.g. g502)")
    p_dev.set_defaults(func=cmd_device)

    p_daemon = sub.add_parser("daemon", help="run the background service in this process")
    p_daemon.add_argument("--no-tray", action="store_true", help="no system tray icon")
    p_daemon.set_defaults(func=cmd_daemon)

    sub.add_parser("start", help="start the background service").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="stop the background service").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="show what the background service is doing").set_defaults(func=cmd_status)
    sub.add_parser("install-startup", help="start the service when you log in").set_defaults(
        func=cmd_install_startup
    )
    sub.add_parser("uninstall-startup", help="don't start the service at login").set_defaults(
        func=cmd_uninstall_startup
    )

    args = parser.parse_args(argv)
    if args.func is not cmd_daemon:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
