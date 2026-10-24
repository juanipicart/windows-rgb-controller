"""Backend-agnostic device interface.

Every backend (OpenRGB, native HID, Windows Dynamic Lighting...) exposes its
devices through this interface so the effects engine never cares where a
device comes from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rgbctl.color import Color

if TYPE_CHECKING:
    from rgbctl.effects import Effect


class Device(ABC):
    name: str
    # Unique within a session (set by Session): the name, plus " #2", " #3"...
    # when several devices share one, like identical RAM sticks.
    key: str
    kind: str
    led_count: int
    backend: str
    # False once the device has failed in a way only reconnecting can fix
    # (e.g. USB handle gone after unplug or sleep).
    healthy: bool = True
    # Devices that run their own built-in effects instead of taking frames
    # (e.g. their lighting writes go to flash). The engine calls
    # apply_effect() for them rather than set_colors().
    hardware_effects: bool = False

    @abstractmethod
    def set_colors(self, colors: Sequence[Color]) -> None:
        """Set every LED; len(colors) must equal led_count."""

    def apply_effect(self, effect: Effect, brightness: float) -> None:
        raise NotImplementedError

    def fill(self, color: Color) -> None:
        self.set_colors([color] * self.led_count)

    def __repr__(self) -> str:
        return f"<{self.backend}:{self.kind} {self.name!r} leds={self.led_count}>"


class Backend(ABC):
    name: str

    @abstractmethod
    def discover(self) -> list[Device]:
        """Return the devices this backend can control."""

    def close(self) -> None:
        pass
