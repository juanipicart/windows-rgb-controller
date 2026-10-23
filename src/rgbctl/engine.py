"""Frame loop that renders an effect onto every device."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from rgbctl.device import Device
from rgbctl.effects import Effect, scale

log = logging.getLogger(__name__)


def layout(devices: Sequence[Device], sync: bool = False) -> list[list[float]]:
    """Assign each LED a position in [0, 1) on the virtual strip.

    span (default): each device gets an equal-width slot, in list order, so
    effects flow from one device into the next.
    sync: every device spans the whole strip, so all devices show the same
    pattern at the same time.
    """
    n = len(devices)
    positions = []
    for i, dev in enumerate(devices):
        count = dev.led_count
        if sync:
            positions.append([j / count for j in range(count)])
        else:
            positions.append([(i + j / count) / n for j in range(count)])
    return positions


class Engine:
    def __init__(
        self,
        devices: Sequence[Device],
        fps: float = 30.0,
        brightness: float = 1.0,
        sync: bool = False,
    ) -> None:
        self.devices = [d for d in devices if d.led_count > 0]
        self.positions = layout(self.devices, sync)
        self.fps = fps
        self.brightness = brightness

    def render_frame(self, effect: Effect, t: float) -> None:
        for dev, pos in zip(self.devices, self.positions):
            colors = effect.render(pos, t)
            if self.brightness < 1.0:
                colors = [scale(c, self.brightness) for c in colors]
            try:
                dev.set_colors(colors)
            except Exception as exc:  # keep animating the other devices
                log.warning("%s: %s", dev.name, exc)

    def run(self, effect: Effect, duration: float | None = None) -> None:
        frame_time = 1.0 / self.fps
        start = time.perf_counter()
        next_frame = start
        while True:
            now = time.perf_counter()
            t = now - start
            if duration is not None and t >= duration:
                return
            self.render_frame(effect, t)
            next_frame += frame_time
            delay = next_frame - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                # Fell behind (slow device); resync instead of bursting.
                next_frame = time.perf_counter()
