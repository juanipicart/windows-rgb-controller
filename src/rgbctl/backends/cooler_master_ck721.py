"""Native driver for the Cooler Master CK721 keyboard (wired, USB 2516:016D).

Captured from MasterPlus+ (firmware release 1.48): setting a solid color
sends two 64-byte reports to HID interface 3 (usage page 0xFF00):

    01 81 C8 00 01 00                                  prepare
    01 81 00 00 0A 00 | FF FF 02 RR GG BB 00 00 00 06  10-byte payload, 02 = static

This sets the keyboard's *built-in* static effect and the keyboard stores
it in non-volatile memory (verified: the color survives a full power-off).
There is no per-key streaming on this firmware (SignalRGB's old 0x0C
direct-mode packets are ignored), so the keyboard is exposed as a single
"LED" that only accepts static colors, and every write is deduplicated and
rate limited to protect the keyboard's flash from wear.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

import hid

from rgbctl.color import Color
from rgbctl.device import Backend, Device

log = logging.getLogger(__name__)

VENDOR_COOLER_MASTER = 0x2516
PRODUCT_CK721_WIRED = 0x016D
RGB_INTERFACE = 3
RGB_USAGE_PAGE = 0xFF00

REPORT_SIZE = 64
EFFECT_STATIC = 0x02
# Each write lands in the keyboard's flash; never write more often than this.
MIN_WRITE_INTERVAL = 1.0

# Last color written, kept for the whole process: the daemon recreates device
# objects on every reconnect, and the keyboard keeps its color across those.
_last_written: Color | None = None


class CK721Device(Device):
    backend = "native"
    kind = "keyboard"
    name = "Cooler Master CK721"
    led_count = 1
    static_only = True

    def __init__(self, path: bytes) -> None:
        self._hid = hid.device()
        self._hid.open_path(path)
        self._pending: Color | None = None
        self._last_write = 0.0

    def set_colors(self, colors: Sequence[Color]) -> None:
        if len(colors) != 1:
            raise ValueError(f"{self.name}: expected 1 color, got {len(colors)}")
        self._pending = colors[0]
        self._maybe_write()

    def _maybe_write(self) -> None:
        global _last_written
        color = self._pending
        if color is None or color == _last_written:
            self._pending = None
            return
        if time.monotonic() - self._last_write < MIN_WRITE_INTERVAL:
            return  # keep it pending; the engine calls again within a second
        try:
            self._report(0x01, 0x81, 0xC8, 0x00, 0x01, 0x00)
            self._report(0x01, 0x81, 0x00, 0x00, 0x0A, 0x00,
                         0xFF, 0xFF, EFFECT_STATIC, color.r, color.g, color.b, 0x00, 0x00, 0x00, 0x06)
        except OSError as exc:
            if self.healthy:
                log.warning("%s: USB write failed: %s", self.name, exc)
            self.healthy = False
            return
        log.debug("%s: wrote %s", self.name, color)
        _last_written, self._pending = color, None
        self._last_write = time.monotonic()

    def _report(self, *payload: int) -> None:
        # Leading 0x00 is the HID report ID; hidapi strips it before sending.
        if self._hid.write([0x00, *payload] + [0x00] * (REPORT_SIZE - len(payload))) < 0:
            raise OSError("hid write failed")

    def close(self) -> None:
        try:
            self._hid.close()
        except OSError:
            pass


class CK721Backend(Backend):
    name = "native"

    def __init__(self) -> None:
        self._devices: list[CK721Device] = []

    def discover(self) -> list[Device]:
        if not self._devices:
            for info in hid.enumerate(VENDOR_COOLER_MASTER, PRODUCT_CK721_WIRED):
                if info["interface_number"] == RGB_INTERFACE and info["usage_page"] == RGB_USAGE_PAGE:
                    self._devices.append(CK721Device(info["path"]))
                    break
        return list(self._devices)

    @property
    def claimed_names(self) -> set[str]:
        return {d.name for d in self._devices}

    def close(self) -> None:
        for dev in self._devices:
            dev.close()
        self._devices.clear()
