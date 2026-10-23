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
    # What a static-only device should show for this effect; None = leave it.
    static_color: Color | None = None

    @abstractmethod
    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        """Colors for LEDs at the given positions, at time t (seconds)."""


class Static(Effect):
    name = "static"
    animated = False

    def __init__(self, color: Color) -> None:
        self.color = color
        self.static_color = color

    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        return [self.color] * len(positions)


class Breathing(Effect):
    name = "breathing"

    def __init__(self, color: Color, period: float = 4.0) -> None:
        self.color = color
        self.period = period
        self.static_color = color

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


EFFECT_NAMES = ("static", "breathing", "rainbow", "wave", "off")

# A spec is the serializable description of what's running: shared by the
# CLI, the daemon's saved state and the tray menu.
DEFAULT_SPEC: dict[str, Any] = {
    "name": "wave",
    "color": "purple",
    "period": None,  # None = the effect's own default
    "spread": 1.0,
    "sync": True,
    "brightness": 1.0,
}


def normalize_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Fill defaults and validate; raises ValueError on bad input."""
    out = {**DEFAULT_SPEC, **{k: v for k, v in spec.items() if k in DEFAULT_SPEC}}
    if out["name"] not in EFFECT_NAMES:
        raise ValueError(f"unknown effect {out['name']!r}")
    Color.parse(out["color"])
    if out["period"] is not None and float(out["period"]) <= 0:
        raise ValueError("period must be positive")
    out["brightness"] = min(max(float(out["brightness"]), 0.0), 1.0)
    out["sync"] = bool(out["sync"])
    return out


def from_spec(spec: Mapping[str, Any]) -> Effect:
    spec = normalize_spec(spec)
    color = Color.parse(spec["color"])
    period = spec["period"]
    match spec["name"]:
        case "static":
            return Static(color)
        case "off":
            return Static(Color(0, 0, 0))
        case "breathing":
            return Breathing(color, period=period or 4.0)
        case "rainbow":
            return Rainbow(period=period or 10.0)
        case "wave":
            return Wave(period=period or 5.0, spread=spec["spread"])
    raise AssertionError(spec["name"])
