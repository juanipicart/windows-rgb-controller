"""Frame loop that renders an effect onto every device."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from enum import Enum

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


class StopReason(Enum):
    DURATION = "duration"  # requested run time elapsed
    STOPPED = "stopped"  # stop() was called
    UNHEALTHY = "unhealthy"  # devices kept failing; caller should reconnect
    RESUMED = "resumed"  # wall clock jumped (sleep/hibernate); devices may have reset


class Engine:
    IDLE_INTERVAL = 1.0  # how often static effects are re-sent
    UNHEALTHY_AFTER = 2.0  # seconds of continuous device errors before giving up
    RESUME_GAP = 5.0  # wall-clock jump that indicates the PC was asleep

    def __init__(
        self,
        devices: Sequence[Device],
        fps: float = 30.0,
        brightness: float = 1.0,
        sync: bool = False,
        effect: Effect | None = None,
    ) -> None:
        self.devices = [d for d in devices if d.led_count > 0]
        self.fps = fps
        self.brightness = brightness
        self.sync = sync
        self.positions = layout(self.devices, sync)
        self.effect = effect
        self._wake = threading.Event()
        self._stopped = threading.Event()

    def update(
        self,
        effect: Effect | None = None,
        brightness: float | None = None,
        sync: bool | None = None,
    ) -> None:
        """Change what's running; safe to call from another thread."""
        if effect is not None:
            self.effect = effect
        if brightness is not None:
            self.brightness = brightness
        if sync is not None and sync != self.sync:
            self.sync = sync
            self.positions = layout(self.devices, sync)
        self._wake.set()

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()

    def wait_stopped(self, timeout: float) -> bool:
        return self._stopped.wait(timeout)

    def render_frame(self, effect: Effect, t: float, failing: set[str] | None = None) -> bool:
        """Render one frame; returns False if any device failed."""
        ok = True
        brightness = self.brightness
        for dev, pos in zip(self.devices, self.positions):
            if dev.static_only:
                if effect.static_color is None:
                    continue
                colors = [effect.static_color] * dev.led_count
            else:
                colors = effect.render(pos, t)
            if brightness < 1.0:
                colors = [scale(c, brightness) for c in colors]
            try:
                dev.set_colors(colors)
            except Exception as exc:  # keep animating the other devices
                ok = False
                if failing is None or dev.name not in failing:
                    log.warning("%s: %s", dev.name, exc)
                if failing is not None:
                    failing.add(dev.name)
            if not dev.healthy:
                ok = False
        return ok

    def run(self, effect: Effect | None = None, duration: float | None = None) -> StopReason:
        if effect is not None:
            self.effect = effect
        start = time.perf_counter()
        next_frame = start
        last_wall = time.time()
        failing_since: float | None = None
        failing: set[str] = set()

        while not self._stopped.is_set():
            now = time.perf_counter()
            t = now - start
            if duration is not None and t >= duration:
                return StopReason.DURATION

            wall = time.time()
            if wall - last_wall > self.RESUME_GAP:
                return StopReason.RESUMED
            last_wall = wall

            effect = self.effect
            interval = self.IDLE_INTERVAL
            if effect is not None:
                if self.render_frame(effect, t, failing):
                    failing_since = None
                    failing.clear()
                elif failing_since is None:
                    failing_since = now
                elif now - failing_since >= self.UNHEALTHY_AFTER:
                    return StopReason.UNHEALTHY
                if effect.animated:
                    interval = 1.0 / self.fps

            next_frame += interval
            delay = next_frame - time.perf_counter()
            if delay <= 0:
                # Fell behind (slow device); resync instead of bursting.
                next_frame = time.perf_counter()
                delay = 0
            if self._wake.wait(delay):
                # Settings changed: render the new state right away.
                self._wake.clear()
                next_frame = time.perf_counter()
        return StopReason.STOPPED
