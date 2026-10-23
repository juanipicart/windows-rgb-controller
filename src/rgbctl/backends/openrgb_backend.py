"""Backend that talks to an OpenRGB SDK server over its TCP protocol.

This is the bootstrap backend: it gives us every device OpenRGB supports
(including risky SMBus devices such as RAM) while native drivers get written.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from openrgb import OpenRGBClient
from openrgb.orgb import Device as ORGBDevice
from openrgb.utils import RGBColor

from rgbctl.color import Color
from rgbctl.config import ZoneSize
from rgbctl.device import Backend, Device

log = logging.getLogger(__name__)

DEFAULT_EXE = Path(r"C:\Program Files\OpenRGB\OpenRGB.exe")


class OpenRGBDevice(Device):
    backend = "openrgb"

    def __init__(self, dev: ORGBDevice) -> None:
        self._dev = dev
        self.name = dev.name
        self.kind = dev.type.name.lower()
        self.led_count = len(dev.leds)
        self._direct = False

    def set_colors(self, colors: Sequence[Color]) -> None:
        if len(colors) != self.led_count:
            raise ValueError(f"{self.name}: expected {self.led_count} colors, got {len(colors)}")
        if not self._direct:
            # Switch to a host-controlled ("Direct"/"Custom") mode so per-LED
            # writes take effect instead of the device's built-in effect.
            self._dev.set_custom_mode()
            self._direct = True
        self._dev.set_colors([RGBColor(c.r, c.g, c.b) for c in colors], fast=True)


class OpenRGBBackend(Backend):
    name = "openrgb"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6742,
        exe: Path = DEFAULT_EXE,
        launch: bool = True,
        zone_sizes: Sequence[ZoneSize] = (),
        exclude: set[str] = frozenset(),
    ) -> None:
        self.host = host
        self.port = port
        self.exe = exe
        self.launch = launch
        self.zone_sizes = list(zone_sizes)
        self.exclude = set(exclude)
        self._client: OpenRGBClient | None = None

    def _try_connect(self) -> OpenRGBClient | None:
        try:
            return OpenRGBClient(self.host, self.port, name="rgbctl")
        except (ConnectionRefusedError, TimeoutError):
            return None

    def _launch_server(self, timeout: float) -> OpenRGBClient:
        if not self.exe.exists():
            raise RuntimeError(f"OpenRGB not found at {self.exe}")
        subprocess.Popen(
            [str(self.exe), "--server", "--server-port", str(self.port), "--startminimized"],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        deadline = time.monotonic() + timeout
        while (client := self._try_connect()) is None:
            if time.monotonic() > deadline:
                raise RuntimeError("OpenRGB server did not start in time")
            time.sleep(0.5)
        return client

    def _wait_for_detection(self, client: OpenRGBClient, settle: float, timeout: float) -> None:
        # A freshly launched server accepts connections before hardware
        # detection finishes, so wait until the device count stops changing.
        deadline = time.monotonic() + timeout
        last_count, stable_since = -1, time.monotonic()
        while time.monotonic() < deadline:
            client.update()
            count = len(client.devices)
            if count != last_count:
                last_count, stable_since = count, time.monotonic()
            elif count > 0 and time.monotonic() - stable_since >= settle:
                return
            time.sleep(0.5)

    def _apply_zone_sizes(self, client: OpenRGBClient) -> None:
        for size in self.zone_sizes:
            matches = [d for d in client.devices if d.name == size.device]
            if not matches:
                log.warning("zone config: no device named %r", size.device)
                continue
            for dev in matches:
                zone = next((z for z in dev.zones if z.name == size.zone), None)
                if zone is None:
                    log.warning("zone config: %r has no zone %r", size.device, size.zone)
                elif len(zone.leds) != size.leds:
                    zone.resize(size.leds)
        client.update()

    def connect(self, settle: float = 3.0, timeout: float = 30.0) -> None:
        client = self._try_connect()
        if client is None:
            if not self.launch:
                raise RuntimeError(
                    f"No OpenRGB server on {self.host}:{self.port}. "
                    "Start OpenRGB with the SDK server enabled (OpenRGB.exe --server)."
                )
            client = self._launch_server(timeout)
            self._wait_for_detection(client, settle, timeout)
        self._apply_zone_sizes(client)
        self._client = client

    def discover(self) -> list[Device]:
        if self._client is None:
            self.connect()
        assert self._client is not None
        # Devices with unsized ARGB zones report 0 LEDs; keep them so they're
        # visible, callers skip them when writing colors.
        return [OpenRGBDevice(d) for d in self._client.devices if d.name not in self.exclude]

    def close(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client = None
