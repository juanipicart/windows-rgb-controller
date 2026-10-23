"""User configuration, loaded from TOML.

Search order: --config path, ./rgbctl.toml, %APPDATA%/rgbctl/config.toml.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ZoneSize:
    device: str
    zone: str
    leds: int


@dataclass(slots=True)
class Config:
    openrgb_host: str = "127.0.0.1"
    openrgb_port: int = 6742
    zones: list[ZoneSize] = field(default_factory=list)
    path: Path | None = None


def default_paths() -> list[Path]:
    paths = [Path.cwd() / "rgbctl.toml"]
    if appdata := os.environ.get("APPDATA"):
        paths.append(Path(appdata) / "rgbctl" / "config.toml")
    return paths


def load(path: Path | None = None) -> Config:
    candidates = [path] if path else default_paths()
    for candidate in candidates:
        if candidate.is_file():
            break
    else:
        if path:
            raise FileNotFoundError(path)
        return Config()

    with candidate.open("rb") as f:
        data = tomllib.load(f)

    openrgb = data.get("openrgb", {})
    return Config(
        openrgb_host=openrgb.get("host", "127.0.0.1"),
        openrgb_port=openrgb.get("port", 6742),
        zones=[ZoneSize(z["device"], z["zone"], int(z["leds"])) for z in data.get("zones", [])],
        path=candidate,
    )
