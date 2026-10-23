from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rgbctl import config
from rgbctl.backends.corsair_lighting_node import LightingNodeBackend
from rgbctl.backends.openrgb_backend import OpenRGBBackend
from rgbctl.color import Color
from rgbctl.device import Backend, Device
from rgbctl.effects import Breathing, Effect, Rainbow, Static, Wave
from rgbctl.engine import Engine


class Session:
    """All backends for one run; native drivers claim devices first."""

    def __init__(self, args: argparse.Namespace) -> None:
        cfg = config.load(args.config)
        self.backends: list[Backend] = []
        self.devices: list[Device] = []
        claimed: set[str] = set()

        if cfg.lighting_node_channels is not None:
            node = LightingNodeBackend(cfg.lighting_node_channels)
            self._add(node)
            claimed |= node.claimed_names

        self._add(
            OpenRGBBackend(
                host=cfg.openrgb_host,
                port=args.port or cfg.openrgb_port,
                launch=not args.no_launch,
                zone_sizes=cfg.zones,
                exclude=claimed,
            )
        )

    def _add(self, backend: Backend) -> None:
        self.backends.append(backend)
        self.devices.extend(backend.discover())

    def close(self) -> None:
        for backend in self.backends:
            backend.close()


def cmd_list(args: argparse.Namespace) -> int:
    session = Session(args)
    devices = session.devices
    try:
        if not devices:
            print("No devices found.")
            return 1
        for i, dev in enumerate(devices):
            note = "  <- no LEDs sized, configure zones" if dev.led_count == 0 else ""
            print(f"[{i}] {dev.name:<40} {dev.kind:<12} {dev.led_count:>4} LEDs  ({dev.backend}){note}")
        return 0
    finally:
        session.close()


def cmd_set(args: argparse.Namespace) -> int:
    color = Color.parse(args.color)
    session = Session(args)
    devices = session.devices
    try:
        failed = 0
        for dev in devices:
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


def _build_effect(args: argparse.Namespace) -> Effect:
    color = Color.parse(args.color) if args.color else Color.parse("purple")
    match args.name:
        case "static":
            return Static(color)
        case "breathing":
            return Breathing(color, period=args.period or 4.0)
        case "rainbow":
            return Rainbow(period=args.period or 10.0)
        case "wave":
            return Wave(period=args.period or 5.0, spread=args.spread)
    raise ValueError(args.name)


def cmd_effect(args: argparse.Namespace) -> int:
    effect = _build_effect(args)
    session = Session(args)
    devices = session.devices
    engine = Engine(devices, fps=args.fps, brightness=args.brightness, sync=args.sync)
    print(f"Running {args.name} on {len(engine.devices)} devices at {args.fps:g} fps (Ctrl+C to stop)")
    try:
        engine.run(effect, duration=args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rgbctl", description="Control your PC's RGB lighting.")
    parser.add_argument("--config", type=Path, help="config file (default: ./rgbctl.toml)")
    parser.add_argument("--port", type=int, help="OpenRGB SDK server port (overrides config)")
    parser.add_argument("--no-launch", action="store_true", help="don't auto-start OpenRGB")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list detected devices").set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="set every device to one color")
    p_set.add_argument("color", help="name (red), hex (#ff0000) or r,g,b")
    p_set.set_defaults(func=cmd_set)

    p_fx = sub.add_parser("effect", help="run an animated effect")
    p_fx.add_argument("name", choices=["static", "breathing", "rainbow", "wave"])
    p_fx.add_argument("color", nargs="?", help="color for static/breathing")
    p_fx.add_argument("--period", type=float, help="seconds per cycle")
    p_fx.add_argument("--spread", type=float, default=1.0, help="rainbows across the strip (wave)")
    p_fx.add_argument("--sync", action="store_true", help="every device shows the same pattern")
    p_fx.add_argument("--fps", type=float, default=30.0)
    p_fx.add_argument("--brightness", type=float, default=1.0, help="0.0-1.0")
    p_fx.add_argument("--duration", type=float, help="stop after N seconds")
    p_fx.set_defaults(func=cmd_effect)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
