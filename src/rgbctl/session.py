"""A Session opens every configured backend and collects their devices."""

from __future__ import annotations

from rgbctl.backends.corsair_lighting_node import LightingNodeBackend
from rgbctl.backends.openrgb_backend import OpenRGBBackend
from rgbctl.config import Config
from rgbctl.device import Backend, Device


class Session:
    """All backends for one run; native drivers claim devices first."""

    def __init__(self, cfg: Config, port: int | None = None, launch_openrgb: bool = True) -> None:
        self.backends: list[Backend] = []
        self.devices: list[Device] = []
        claimed: set[str] = set()
        try:
            if cfg.lighting_node_channels is not None:
                node = LightingNodeBackend(cfg.lighting_node_channels)
                self._add(node)
                claimed |= node.claimed_names

            self._add(
                OpenRGBBackend(
                    host=cfg.openrgb_host,
                    port=port or cfg.openrgb_port,
                    launch=launch_openrgb,
                    zone_sizes=cfg.zones,
                    exclude=claimed,
                )
            )
        except BaseException:
            self.close()
            raise

    @property
    def openrgb_device_count(self) -> int:
        return sum(b.server_device_count for b in self.backends if isinstance(b, OpenRGBBackend))

    def _add(self, backend: Backend) -> None:
        self.backends.append(backend)
        self.devices.extend(backend.discover())

    def close(self) -> None:
        for backend in self.backends:
            backend.close()
        self.backends.clear()
