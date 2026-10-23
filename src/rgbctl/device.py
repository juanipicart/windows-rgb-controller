"""Backend-agnostic device interface.

Every backend (OpenRGB, native HID, Windows Dynamic Lighting...) exposes its
devices through this interface so the effects engine never cares where a
device comes from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from rgbctl.color import Color


class Device(ABC):
    name: str
    kind: str
    led_count: int
    backend: str
    # False once the device has failed in a way only reconnecting can fix
    # (e.g. USB handle gone after unplug or sleep).
    healthy: bool = True
    # Devices that can only hold a fixed color (e.g. writes go to flash).
    # The engine sends them Effect.static_color instead of animation frames.
    static_only: bool = False

    @abstractmethod
    def set_colors(self, colors: Sequence[Color]) -> None:
        """Set every LED; len(colors) must equal led_count."""

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
