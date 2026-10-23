"""Native driver for Corsair Lighting Node Core / Lighting Node Pro.

Protocol (same family as the Commander Pro), 64-byte HID reports:

    0x32 DIRECT      channel, start, count, color_plane, <count bytes>
    0x33 COMMIT      0xFF
    0x38 PORT_STATE  channel, 0x01 hardware | 0x02 software

Colors go out as separate red/green/blue planes, and every packet gets an
acknowledgement report.

Throughput is bound by the controller accepting one OUT report every 2 ms
(measured: each write blocks for 2.00 ms). Two things keep us near that floor:

- 51 LEDs per packet instead of the conventional 50 (verified on a Lighting
  Node Core, fw 0.9.16). 204 LEDs = 4 chunks x 3 planes + commit = 13
  packets, ~26 ms/frame, vs 16 packets/~32 ms.
- Acks are drained without blocking instead of waited on after each write
  (OpenRGB's approach costs another ~7 ms/frame). Windows' HID driver
  buffers input reports, so they are simply collected before the next frame.

Each device owns a writer thread holding only the latest frame, so a slow
USB write never stalls the effects engine; stale frames are simply dropped.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence

import hid

from rgbctl.color import Color
from rgbctl.device import Backend, Device

log = logging.getLogger(__name__)

VENDOR_CORSAIR = 0x1B1C
PRODUCTS = {
    0x0C1A: "Corsair Lighting Node Core",
    0x0C0B: "Corsair Lighting Node Pro",
}

PKT_DIRECT = 0x32
PKT_COMMIT = 0x33
PKT_PORT_STATE = 0x38
PORT_STATE_HARDWARE = 0x01
PORT_STATE_SOFTWARE = 0x02

LEDS_PER_PACKET = 51
REPORT_SIZE = 64
# Without a commit for a while the controller falls back to its hardware
# effect; re-commit periodically while idle to hold the last frame.
KEEPALIVE_INTERVAL = 5.0


class LightingNodeDevice(Device):
    backend = "native"
    kind = "ledstrip"

    def __init__(self, path: bytes, name: str, channel_leds: Sequence[int]) -> None:
        self.name = name
        self.channel_leds = [n for n in channel_leds]
        self.led_count = sum(self.channel_leds)

        self._hid = hid.device()
        self._hid.open_path(path)
        self._hid.set_nonblocking(1)
        self._lock = threading.Condition()
        self._pending: list[Color] | None = None
        self._closed = False
        self._software_mode = False
        self._last_commit = 0.0
        self._thread = threading.Thread(target=self._writer, name=f"{name} writer", daemon=True)
        self._thread.start()

    # -- public API ---------------------------------------------------------

    def set_colors(self, colors: Sequence[Color]) -> None:
        if len(colors) != self.led_count:
            raise ValueError(f"{self.name}: expected {self.led_count} colors, got {len(colors)}")
        with self._lock:
            self._pending = list(colors)
            self._lock.notify()

    def flush(self, timeout: float = 1.0) -> None:
        """Block until the latest frame has been written."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._pending is not None and time.monotonic() < deadline:
                self._lock.wait(0.01)

    def close(self) -> None:
        self.flush()
        with self._lock:
            self._closed = True
            self._lock.notify()
        self._thread.join(timeout=2.0)
        self._hid.close()

    # -- writer thread ------------------------------------------------------

    def _writer(self) -> None:
        while True:
            with self._lock:
                self._lock.wait_for(lambda: self._pending is not None or self._closed, KEEPALIVE_INTERVAL)
                if self._closed:
                    return
                frame, self._pending = self._pending, None
            try:
                if frame is not None:
                    self._write_frame(frame)
                elif self._software_mode and time.monotonic() - self._last_commit >= KEEPALIVE_INTERVAL:
                    self._send_batch([self._commit_packet()])
            except OSError as exc:
                log.warning("%s: USB write failed: %s", self.name, exc)
            finally:
                with self._lock:
                    self._lock.notify_all()  # wake flush()

    def _write_frame(self, colors: Sequence[Color]) -> None:
        packets = []
        if not self._software_mode:
            for channel in range(len(self.channel_leds)):
                packets.append(self._packet(PKT_PORT_STATE, channel, PORT_STATE_SOFTWARE))
        offset = 0
        for channel, count in enumerate(self.channel_leds):
            chunk = colors[offset : offset + count]
            offset += count
            planes = ([c.r for c in chunk], [c.g for c in chunk], [c.b for c in chunk])
            for start in range(0, count, LEDS_PER_PACKET):
                n = min(LEDS_PER_PACKET, count - start)
                for plane_id, plane in enumerate(planes):
                    packets.append(
                        self._packet(PKT_DIRECT, channel, start, n, plane_id, *plane[start : start + n])
                    )
        packets.append(self._commit_packet())
        self._send_batch(packets)
        self._software_mode = True

    def _send_batch(self, packets: list[list[int]]) -> None:
        self._drain_acks()
        for pkt in packets:
            self._hid.write(pkt)
        self._last_commit = time.monotonic()

    def _drain_acks(self) -> None:
        # Collect acks from the previous batch without waiting for any.
        while ack := self._hid.read(REPORT_SIZE):
            if ack[0] != 0x00:
                log.debug("%s: controller returned status 0x%02x", self.name, ack[0])

    @staticmethod
    def _packet(*payload: int) -> list[int]:
        # Leading 0x00 is the HID report ID; hidapi strips it before sending.
        return [0x00, *payload] + [0x00] * (REPORT_SIZE - len(payload))

    def _commit_packet(self) -> list[int]:
        return self._packet(PKT_COMMIT, 0xFF)


class LightingNodeBackend(Backend):
    name = "native"

    def __init__(self, channel_leds: Sequence[int]) -> None:
        self.channel_leds = list(channel_leds)
        self._devices: list[LightingNodeDevice] = []

    def discover(self) -> list[Device]:
        if not self._devices:
            for info in hid.enumerate(VENDOR_CORSAIR, 0):
                name = PRODUCTS.get(info["product_id"])
                if name is None:
                    continue
                self._devices.append(LightingNodeDevice(info["path"], name, self.channel_leds))
        return list(self._devices)

    @property
    def claimed_names(self) -> set[str]:
        """Device names this backend owns, so other backends can skip them."""
        return {d.name for d in self._devices}

    def close(self) -> None:
        for dev in self._devices:
            dev.close()
        self._devices.clear()
