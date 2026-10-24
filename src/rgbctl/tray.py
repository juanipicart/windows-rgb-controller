"""System tray icon for the daemon."""

from __future__ import annotations

import threading
from typing import Any

import pystray
from PIL import Image, ImageDraw

from rgbctl.color import Color
from rgbctl.colorpicker import choose_color
from rgbctl.daemon import Daemon
from rgbctl.effects import hsv

EFFECTS = [
    ("Wave (synced)", {"name": "wave", "sync": True}),
    ("Wave (flowing)", {"name": "wave", "sync": False}),
    ("Rainbow", {"name": "rainbow"}),
    ("Breathing", {"name": "breathing"}),
    ("Static", {"name": "static"}),
]
# Effects that ignore the color; picking one switches to static.
COLORLESS = {"wave", "rainbow", "off"}
BRIGHTNESS = [100, 75, 50, 25]


def icon_image(size: int = 64) -> Image.Image:
    """A rainbow ring, drawn instead of shipping an .ico file."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    steps = 36
    for i in range(steps):
        c = hsv(i / steps)
        start = i * 360 / steps
        draw.pieslice((2, 2, size - 2, size - 2), start, start + 360 / steps + 1, fill=(c.r, c.g, c.b))
    inset = size // 4
    draw.ellipse((inset, inset, size - inset, size - inset), fill=(0, 0, 0, 0))
    return img


def _matches(spec: dict[str, Any], want: dict[str, Any]) -> bool:
    return all(spec.get(k) == v for k, v in want.items())


def run_tray(daemon: Daemon) -> None:
    def setter(changes: dict[str, Any]):
        def action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            daemon.apply(changes)

        return action

    def checker(want: dict[str, Any]):
        return lambda item: _matches(daemon.spec, want)

    effect_items = [
        # period=None so each effect falls back to its own default speed.
        pystray.MenuItem(label, setter({**changes, "period": None}), checked=checker(changes), radio=True)
        for label, changes in EFFECTS
    ]
    picking = threading.Lock()

    def pick_color() -> None:
        # The dialog is modal; holding the lock stops a second one opening.
        if not picking.acquire(blocking=False):
            return
        try:
            color = choose_color(Color.parse(daemon.spec["color"]))
            if color is None:
                return
            changes: dict[str, Any] = {"color": str(color)}
            if daemon.spec["name"] in COLORLESS:
                changes |= {"name": "static", "period": None}
            daemon.apply(changes)
        finally:
            picking.release()

    def color_action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        # Off the tray thread, so the menu keeps working while the dialog is up.
        threading.Thread(target=pick_color, name="color-picker", daemon=True).start()
    brightness_items = [
        pystray.MenuItem(
            f"{pct}%", setter({"brightness": pct / 100}), checked=checker({"brightness": pct / 100}), radio=True
        )
        for pct in BRIGHTNESS
    ]

    def quit_action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        daemon.shutdown()

    menu = pystray.Menu(
        *effect_items,
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Color...", color_action),
        pystray.MenuItem("Brightness", pystray.Menu(*brightness_items)),
        pystray.MenuItem("Off", setter({"name": "off"}), checked=checker({"name": "off"}), radio=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_action),
    )
    icon = pystray.Icon("rgbctl", icon_image(), "rgbctl", menu)
    daemon.on_quit = icon.stop
    icon.run()
