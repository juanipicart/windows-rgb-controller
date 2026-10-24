"""A Session opens every configured backend and collects their devices."""

from __future__ import annotations

import logging

from rgbctl.backends.cooler_master_ck721 import CK721Backend
from rgbctl.backends.corsair_lighting_node import LightingNodeBackend
from rgbctl.backends.openrgb_backend import OpenRGBBackend
from rgbctl.config import Config
from rgbctl.device import Backend, Device

log = logging.getLogger(__name__)


class Session:
    """All backends for one run; native drivers claim devices first."""

    def __init__(
        self,
        cfg: Config,
        port: int | None = None,
        launch_openrgb: bool = True,
        require_openrgb: bool = True,
    ) -> None:
        self.backends: list[Backend] = []
        self.devices: list[Device] = []
        self.openrgb: OpenRGBBackend | None = None
        claimed: set[str] = set()
        try:
            if cfg.lighting_node_channels is not None:
                node = LightingNodeBackend(cfg.lighting_node_channels)
                self._add(node)
                claimed |= node.claimed_names

            if cfg.ck721:
                self._add(CK721Backend())

            openrgb = OpenRGBBackend(
                host=cfg.openrgb_host,
                port=port or cfg.openrgb_port,
                launch=launch_openrgb,
                zone_sizes=cfg.zones,
                exclude=claimed,
            )
            try:
                self._add(openrgb)
                self.openrgb = openrgb
            except (OSError, RuntimeError) as exc:
                if require_openrgb:
                    raise
                # e.g. the service is still on its delayed start after boot;
                # run the native devices meanwhile.
                self.backends.remove(openrgb)
                log.warning("OpenRGB unavailable (%s); continuing with native devices only", exc)
        except BaseException:
            self.close()
            raise
        assign_keys(self.devices)

    def _add(self, backend: Backend) -> None:
        self.backends.append(backend)
        self.devices.extend(backend.discover())

    def close(self) -> None:
        for backend in self.backends:
            backend.close()
        self.backends.clear()


def assign_keys(devices: list[Device]) -> None:
    """Give each device a unique key, numbering repeated names in order."""
    seen: dict[str, int] = {}
    for dev in devices:
        n = seen[dev.name] = seen.get(dev.name, 0) + 1
        dev.key = dev.name if n == 1 else f"{dev.name} #{n}"
