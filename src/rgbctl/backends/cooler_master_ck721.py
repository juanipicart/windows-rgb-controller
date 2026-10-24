"""Native driver for the Cooler Master CK721 keyboard (wired, USB 2516:016D).

Protocol, captured from MasterPlus+ (firmware release 1.48). Lighting is a
settings table written with 64-byte reports to HID interface 3 (usage page
0xFF00):

    01 81 <offset> 00 <len> 00 <data...>    write <len> bytes at <offset>

    offset 200 (0xC8), 1 byte:   active built-in effect number k
    offset 10*k, 10 bytes:       effect k's record
                                 FF FF p1 RR GG BB 00 00 00 p2
                                 (p1/p2 are MasterPlus+ slider values;
                                 its defaults are 02 and 06)

Effect numbers, mapped from MasterPlus+ (Spanish UI names):
    0 Estatico, 1 Onda de arcoiris, 2 Punto de mira, 3 Fundido reactivo,
    5 Estrellas, 6 Nieve, 7 Ciclo de colores, 8 Respiracion, 9 Onda,
    10 Punetazo reactivo, 11 Latido, 12 Bola de fuego, 13 Espectro circular,
    14 Tornado reactivo, 15 Onda de agua (4: not identified)

The table lives in the keyboard's non-volatile memory (verified: a color
survives a full power-off), and there is no per-key streaming on this
firmware (SignalRGB's old 0x0C direct-mode packets are ignored). So instead
of frames, the device receives the effect and switches to the keyboard's
own matching animation; writes happen only when that changes, and at most
once per second, to protect the flash from wear.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import NamedTuple

import hid

from rgbctl.color import Color
from rgbctl.device import Backend, Device
from rgbctl.effects import Effect, scale

log = logging.getLogger(__name__)

VENDOR_COOLER_MASTER = 0x2516
PRODUCT_CK721_WIRED = 0x016D
RGB_INTERFACE = 3
RGB_USAGE_PAGE = 0xFF00

REPORT_SIZE = 64
OFFSET_ACTIVE_EFFECT = 0xC8
RECORD_SIZE = 10
DEFAULT_P1 = 0x02
DEFAULT_P2 = 0x06

HW_STATIC = 0
HW_RAINBOW_WAVE = 1
HW_STARS = 5
HW_COLOR_CYCLE = 7
HW_BREATHING = 8
HW_WAVE = 9
HW_HEARTBEAT = 11

# Each write lands in the keyboard's flash; never write more often than this.
MIN_WRITE_INTERVAL = 1.0


class HardwareState(NamedTuple):
    effect: int
    color: Color | None  # None: leave the effect's stored record as is


# Last state written, kept for the whole process: the daemon recreates device
# objects on every reconnect, and the keyboard keeps its state across those.
_last_written: HardwareState | None = None


def hardware_state(effect: Effect, brightness: float) -> HardwareState | None:
    """The keyboard's closest built-in equivalent of an rgbctl effect."""
    color = getattr(effect, "color", None)
    if color is not None and brightness < 1.0:
        color = scale(color, brightness)
    match effect.name:
        case "static" | "fire":  # also "off" (static black); fire: its representative color
            return HardwareState(HW_STATIC, color)
        case "breathing":
            return HardwareState(HW_BREATHING, color)
        case "wave":
            return HardwareState(HW_RAINBOW_WAVE, None)
        case "rainbow":
            return HardwareState(HW_COLOR_CYCLE, None)
        case "color_wave" | "comet":
            return HardwareState(HW_WAVE, color)
        case "twinkle":
            return HardwareState(HW_STARS, color)
        case "heartbeat":
            return HardwareState(HW_HEARTBEAT, color)
    return None


class CK721Device(Device):
    backend = "native"
    kind = "keyboard"
    name = "Cooler Master CK721"
    led_count = 1
    hardware_effects = True

    def __init__(self, path: bytes) -> None:
        self._hid = hid.device()
        self._hid.open_path(path)
        self._pending: HardwareState | None = None
        self._last_write = 0.0

    def set_colors(self, colors: Sequence[Color]) -> None:
        """Direct color (e.g. `rgbctl set`): the built-in static effect."""
        if len(colors) != 1:
            raise ValueError(f"{self.name}: expected 1 color, got {len(colors)}")
        self._pending = HardwareState(HW_STATIC, colors[0])
        self._maybe_write()

    def apply_effect(self, effect: Effect, brightness: float) -> None:
        state = hardware_state(effect, brightness)
        if state is not None:
            self._pending = state
        self._maybe_write()

    def _maybe_write(self) -> None:
        global _last_written
        state = self._pending
        if state is None or state == _last_written:
            self._pending = None
            return
        if time.monotonic() - self._last_write < MIN_WRITE_INTERVAL:
            return  # keep it pending; the engine calls again within a second
        try:
            # Same order as MasterPlus+: select the effect, then its record.
            self._write_table(OFFSET_ACTIVE_EFFECT, [state.effect])
            if state.color is not None:
                c = state.color
                record = [0xFF, 0xFF, DEFAULT_P1, c.r, c.g, c.b, 0x00, 0x00, 0x00, DEFAULT_P2]
                self._write_table(RECORD_SIZE * state.effect, record)
        except OSError as exc:
            if self.healthy:
                log.warning("%s: USB write failed: %s", self.name, exc)
            self.healthy = False
            return
        log.debug("%s: wrote %s", self.name, state)
        _last_written, self._pending = state, None
        self._last_write = time.monotonic()

    def _write_table(self, offset: int, data: list[int]) -> None:
        payload = [0x01, 0x81, offset, 0x00, len(data), 0x00, *data]
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
