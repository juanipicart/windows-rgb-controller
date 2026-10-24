"""Control window, opened by left-clicking the tray icon.

Tk isn't thread-safe, so the window lives on its own thread and every Tk
call happens there. Other threads only set flags, which the window polls.
Closing the window hides it; the thread stays up for the next open.
"""

from __future__ import annotations

import ctypes
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from PIL import Image, ImageTk

from rgbctl.color import Color
from rgbctl.colorpicker import choose_color
from rgbctl.daemon import Daemon

EFFECTS = [
    ("Wave (synced)", {"name": "wave", "sync": True}),
    ("Wave (flowing)", {"name": "wave", "sync": False}),
    ("Rainbow", {"name": "rainbow"}),
    ("Breathing", {"name": "breathing"}),
    ("Static", {"name": "static"}),
    # These two travel along the strip, so flowing (not synced) is the point.
    ("Color Wave", {"name": "color_wave", "sync": False}),
    ("Comet", {"name": "comet", "sync": False}),
    ("Twinkle", {"name": "twinkle"}),
    ("Heartbeat", {"name": "heartbeat"}),
    ("Fire", {"name": "fire"}),
]
# Effects that ignore the color; picking one switches to static.
COLORLESS = {"wave", "rainbow", "fire", "off"}
POLL_MS = 250


def matches(spec: dict[str, Any], want: dict[str, Any]) -> bool:
    return all(spec.get(k) == v for k, v in want.items())


def toggle_device(spec: dict[str, Any], key: str, on: bool) -> dict[str, Any]:
    """Spec changes for turning one device's lighting on or off."""
    disabled = set(spec["disabled"])
    return {"disabled": sorted(disabled - {key} if on else disabled | {key})}


def color_changes(spec: dict[str, Any], color: Color) -> dict[str, Any]:
    """Spec changes for picking a color, switching to static if it'd be invisible."""
    changes: dict[str, Any] = {"color": str(color)}
    if spec["name"] in COLORLESS:
        changes |= {"name": "static", "period": None}
    return changes


class ControlWindow:
    def __init__(self, daemon: Daemon, icon: Image.Image | None = None) -> None:
        self.daemon = daemon
        self.icon = icon
        self._show = threading.Event()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def show(self) -> None:
        """Open the window (or bring it to the front). Any thread."""
        self._show.set()
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="window", daemon=True)
                self._thread.start()

    def close(self) -> None:
        """Destroy the window for good. Any thread."""
        self._quit.set()

    # -- window thread ------------------------------------------------------

    def _run(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
        except (AttributeError, OSError):
            pass
        root = tk.Tk()
        root.title("rgbctl")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        if self.icon is not None:
            self._icon_photo = ImageTk.PhotoImage(self.icon)  # keep a reference or Tk drops it
            root.iconphoto(True, self._icon_photo)
        self._build(root)
        self._shown_spec: dict[str, Any] | None = None
        self._shown_devices: list[str] | None = None
        self._poll(root)
        root.mainloop()

    def _build(self, root: tk.Tk) -> None:
        frame = ttk.Frame(root, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)

        effects = ttk.LabelFrame(frame, text="Effect", padding=(12, 6))
        effects.grid(row=0, column=0, sticky="ew")
        self.effect_var = tk.IntVar(value=-1)
        options = [*EFFECTS, ("Off", {"name": "off"})]
        for i, (label, changes) in enumerate(options):
            ttk.Radiobutton(
                effects,
                text=label,
                variable=self.effect_var,
                value=i,
                command=lambda c=changes: self._apply({**c, "period": None}),
            ).grid(row=i, column=0, sticky="w", pady=1)
        self._effect_options = options

        color = ttk.LabelFrame(frame, text="Color", padding=(12, 8))
        color.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.swatch = tk.Frame(color, width=36, height=24, relief="solid", borderwidth=1, cursor="hand2")
        self.swatch.grid(row=0, column=0, padx=(0, 8))
        self.swatch.bind("<Button-1>", lambda e: self._pick_color(root))
        self.hex_var = tk.StringVar()
        hex_entry = ttk.Entry(color, textvariable=self.hex_var, width=10)
        hex_entry.grid(row=0, column=1, padx=(0, 8))
        hex_entry.bind("<Return>", lambda e: self._apply_hex())
        hex_entry.bind("<FocusOut>", lambda e: self._apply_hex())
        ttk.Button(color, text="Pick...", command=lambda: self._pick_color(root)).grid(row=0, column=2)

        bright = ttk.LabelFrame(frame, text="Brightness", padding=(12, 8))
        bright.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        bright.columnconfigure(0, weight=1)
        self.bright_var = tk.DoubleVar()
        scale = ttk.Scale(bright, from_=0, to=100, variable=self.bright_var, length=200)
        scale.grid(row=0, column=0, sticky="ew")
        # Apply on release, not while dragging: the keyboard stores every
        # change in flash (its driver rate-limits too, but no need to push it).
        scale.bind("<ButtonRelease-1>", lambda e: self._apply({"brightness": round(self.bright_var.get()) / 100}))
        self.bright_label = ttk.Label(bright, width=5, anchor="e")
        self.bright_label.grid(row=0, column=1)
        self.bright_var.trace_add("write", lambda *a: self.bright_label.config(text=f"{round(self.bright_var.get())}%"))

        self.devices_frame = ttk.LabelFrame(frame, text="Devices", padding=(12, 6))
        self.devices_frame.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.device_vars: dict[str, tk.BooleanVar] = {}

    def _poll(self, root: tk.Tk) -> None:
        if self._quit.is_set():
            root.destroy()
            return
        if self._show.is_set():
            self._show.clear()
            root.deiconify()
            root.lift()
            root.focus_force()
        # Follow changes made from the tray menu or the CLI.
        if self.daemon.devices != self._shown_devices:
            self._shown_devices = list(self.daemon.devices)
            self._build_devices(self._shown_devices)
            self._shown_spec = None  # the new checkboxes need their state
        spec = self.daemon.spec
        if spec != self._shown_spec:
            self._shown_spec = spec
            self._show_spec(spec)
        root.after(POLL_MS, self._poll, root)

    def _build_devices(self, keys: list[str]) -> None:
        for child in self.devices_frame.winfo_children():
            child.destroy()
        self.device_vars = {}
        if not keys:
            ttk.Label(self.devices_frame, text="connecting...").grid(row=0, column=0, sticky="w")
        for i, key in enumerate(keys):
            var = self.device_vars[key] = tk.BooleanVar()
            ttk.Checkbutton(
                self.devices_frame,
                text=key,
                variable=var,
                command=lambda k=key, v=var: self._apply(toggle_device(self.daemon.spec, k, v.get())),
            ).grid(row=i, column=0, sticky="w", pady=1)

    def _show_spec(self, spec: dict[str, Any]) -> None:
        for key, var in self.device_vars.items():
            var.set(key not in spec["disabled"])
        index = next((i for i, (_, want) in enumerate(self._effect_options) if matches(spec, want)), -1)
        self.effect_var.set(index)
        color = Color.parse(spec["color"])
        self.swatch.config(background=str(color))
        if self.hex_var.get().strip().lower() != str(color):
            self.hex_var.set(str(color))
        self.bright_var.set(spec["brightness"] * 100)

    def _apply(self, changes: dict[str, Any]) -> None:
        self.daemon.apply(changes)
        self._shown_spec = None  # redraw on the next poll

    def _apply_hex(self) -> None:
        try:
            color = Color.parse(self.hex_var.get())
        except ValueError:
            self._shown_spec = None  # revert to the current color
            return
        if str(color) != str(Color.parse(self.daemon.spec["color"])):
            self._apply(color_changes(self.daemon.spec, color))

    def _pick_color(self, root: tk.Tk) -> None:
        # Owned by our window, so it's modal to it and opens on top.
        color = choose_color(Color.parse(self.daemon.spec["color"]), owner=int(root.wm_frame(), 16))
        if color is not None:
            self._apply(color_changes(self.daemon.spec, color))
