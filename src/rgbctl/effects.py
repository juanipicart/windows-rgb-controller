"""Lighting effects.

An effect maps (position, time) -> color. Positions are in [0, 1) along a
single virtual strip spanning every device, which is what lets a wave flow
from one device into the next.
"""

from __future__ import annotations

import colorsys
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from rgbctl.color import Color


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return Color(round(r * 255), round(g * 255), round(b * 255))


def scale(color: Color, factor: float) -> Color:
    return Color(round(color.r * factor), round(color.g * factor), round(color.b * factor))


class Effect(ABC):
    name: str
    # Static effects only need re-sending occasionally, not every frame.
    animated: bool = True

    @abstractmethod
    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        """Colors for LEDs at the given positions, at time t (seconds)."""


class Static(Effect):
    name = "static"
    animated = False

    def __init__(self, color: Color) -> None:
        self.color = color

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        return [self.color] * len(positions)


class Breathing(Effect):
    name = "breathing"

    def __init__(self, color: Color, period: float = 4.0) -> None:
        self.color = color
        self.period = period

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        # Squared sine eases in/out so the dim end lingers like real breathing.
        level = math.sin(math.pi * t / self.period) ** 2
        return [scale(self.color, level)] * len(positions)


class Rainbow(Effect):
    """Every LED shows the same hue, cycling over time."""

    name = "rainbow"

    def __init__(self, period: float = 10.0) -> None:
        self.period = period

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        return [hsv(t / self.period)] * len(positions)


class Wave(Effect):
    """A rainbow that travels along the virtual strip."""

    name = "wave"

    def __init__(self, period: float = 5.0, spread: float = 1.0) -> None:
        self.period = period
        self.spread = spread  # how many full rainbows fit across the strip

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        offset = t / self.period
        return [hsv(p * self.spread - offset) for p in positions]


class ColorWave(Effect):
    """Bands of one color travelling along the strip."""

    name = "color_wave"

    def __init__(self, color: Color, period: float = 4.0, spread: float = 1.0) -> None:
        self.color = color
        self.period = period
        self.spread = spread  # how many bands fit across the strip

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        offset = t / self.period
        out = []
        for p in positions:
            level = (math.sin(2 * math.pi * (p * self.spread - offset)) + 1) / 2
            out.append(scale(self.color, 0.1 + 0.9 * level**2))
        return out


class Comet(Effect):
    """A bright head with a fading tail, looping along the strip."""

    name = "comet"
    TAIL = 0.25  # fraction of the strip

    def __init__(self, color: Color, period: float = 3.0) -> None:
        self.color = color
        self.period = period

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        head = (t / self.period) % 1.0
        out = []
        for p in positions:
            behind = (head - p) % 1.0  # distance behind the head, wrapping
            level = (1 - behind / self.TAIL) ** 2 if behind < self.TAIL else 0.0
            out.append(scale(self.color, level))
        return out


def _noise(seed: float) -> float:
    """Cheap deterministic pseudo-random number in [0, 1) for a seed."""
    return math.sin(seed * 12.9898 + 78.233) * 43758.5453 % 1.0


class Twinkle(Effect):
    """LEDs sparkle at random over a dim glow of the color."""

    name = "twinkle"
    BASE = 0.15  # glow between sparkles, so small devices never go fully dark

    def __init__(self, color: Color, period: float = 3.0) -> None:
        self.color = color
        self.period = period  # roughly how often each LED sparkles

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        out = []
        for i, p in enumerate(positions):
            # Each LED gets its own rate and phase, derived from where it is,
            # so the effect stays stateless.
            seed = p * 1000 + i
            rate = 0.6 + 0.8 * _noise(seed)
            phase = (t / (self.period * rate) + _noise(seed + 0.5)) % 1.0
            spark = max(0.0, math.sin(math.pi * phase)) ** 12  # short, sharp peak
            out.append(scale(self.color, self.BASE + (1 - self.BASE) * spark))
        return out


class Heartbeat(Effect):
    """Two quick pulses (lub-dub), then a rest."""

    name = "heartbeat"

    def __init__(self, color: Color, period: float = 1.2) -> None:
        self.color = color
        self.period = period

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        phase = (t / self.period) % 1.0
        level = max(_pulse(phase, 0.0, 0.15), 0.6 * _pulse(phase, 0.22, 0.15))
        return [scale(self.color, 0.05 + 0.95 * level)] * len(positions)


def _pulse(phase: float, start: float, width: float) -> float:
    x = (phase - start) / width
    return math.sin(math.pi * x) ** 2 if 0.0 <= x <= 1.0 else 0.0


class Fire(Effect):
    """Flickering reds, oranges and yellows."""

    name = "fire"
    # Representative color, for devices that can only show one.
    color = Color(255, 60, 0)

    def __init__(self, period: float = 1.0) -> None:
        self.period = period  # lower is a faster flicker

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        s = t / self.period
        out = []
        for i, p in enumerate(positions):
            seed = p * 100 + i * 7.31
            # Sum of sines at unrelated rates: irregular, but smooth.
            heat = (
                math.sin(s * 5.1 + seed)
                + 0.6 * math.sin(s * 9.7 + seed * 1.7)
                + 0.4 * math.sin(s * 17.3 + seed * 2.3)
            ) / 2.0  # about -1..1
            heat = min(max((heat + 1) / 2, 0.0), 1.0)
            out.append(hsv(0.02 + 0.09 * heat, 1.0, 0.45 + 0.55 * heat))
        return out


EFFECT_NAMES = (
    "static",
    "breathing",
    "rainbow",
    "wave",
    "color_wave",
    "comet",
    "twinkle",
    "heartbeat",
    "fire",
    "off",
)

# Seconds per cycle at speed 1.0.
DEFAULT_PERIODS = {
    "breathing": 4.0,
    "rainbow": 10.0,
    "wave": 5.0,
    "color_wave": 4.0,
    "comet": 3.0,
    "twinkle": 3.0,
    "heartbeat": 1.2,
    "fire": 1.0,
}
MIN_SPEED, MAX_SPEED = 0.25, 4.0

# A spec is the serializable description of what's running: shared by the
# CLI, the daemon's saved state and the tray menu.
DEFAULT_SPEC: dict[str, Any] = {
    "name": "wave",
    "color": "purple",
    "period": None,  # seconds per cycle; None = the effect's own default
    "speed": 1.0,  # how fast effect time runs (applied by the engine)
    "spread": 1.0,
    "sync": True,
    "brightness": 1.0,
    "disabled": [],  # device keys kept dark whatever the effect
}


def normalize_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Fill defaults and validate; raises ValueError on bad input."""
    out = {**DEFAULT_SPEC, **{k: v for k, v in spec.items() if k in DEFAULT_SPEC}}
    if out["name"] not in EFFECT_NAMES:
        raise ValueError(f"unknown effect {out['name']!r}")
    Color.parse(out["color"])
    if out["period"] is not None and float(out["period"]) <= 0:
        raise ValueError("period must be positive")
    out["speed"] = min(max(float(out["speed"]), MIN_SPEED), MAX_SPEED)
    out["brightness"] = min(max(float(out["brightness"]), 0.0), 1.0)
    out["sync"] = bool(out["sync"])
    if not isinstance(out["disabled"], (list, tuple, set)):
        raise ValueError("disabled must be a list of device names")
    out["disabled"] = sorted({str(k) for k in out["disabled"]})
    return out


def from_spec(spec: Mapping[str, Any]) -> Effect:
    spec = normalize_spec(spec)
    color = Color.parse(spec["color"])
    period = spec["period"] or DEFAULT_PERIODS.get(spec["name"], 1.0)
    match spec["name"]:
        case "static":
            return Static(color)
        case "off":
            return Static(Color(0, 0, 0))
        case "breathing":
            return Breathing(color, period=period)
        case "rainbow":
            return Rainbow(period=period)
        case "wave":
            return Wave(period=period, spread=spec["spread"])
        case "color_wave":
            return ColorWave(color, period=period, spread=spec["spread"])
        case "comet":
            return Comet(color, period=period)
        case "twinkle":
            return Twinkle(color, period=period)
        case "heartbeat":
            return Heartbeat(color, period=period)
        case "fire":
            return Fire(period=period)
    raise AssertionError(spec["name"])
