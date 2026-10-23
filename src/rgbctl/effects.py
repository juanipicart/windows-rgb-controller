"""Lighting effects.

An effect maps (position, time) -> color. Positions are in [0, 1) along a
single virtual strip spanning every device, which is what lets a wave flow
from one device into the next.
"""

from __future__ import annotations

import colorsys
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

from rgbctl.color import Color


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return Color(round(r * 255), round(g * 255), round(b * 255))


def scale(color: Color, factor: float) -> Color:
    return Color(round(color.r * factor), round(color.g * factor), round(color.b * factor))


class Effect(ABC):
    name: str

    @abstractmethod
    def render(self, positions: Sequence[float], t: float) -> list[Color]:
        """Colors for LEDs at the given positions, at time t (seconds)."""


class Static(Effect):
    name = "static"

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


EFFECTS = {cls.name: cls for cls in (Static, Breathing, Rainbow, Wave)}
