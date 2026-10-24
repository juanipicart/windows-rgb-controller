"""Native Windows color dialog (ChooseColorW), via ctypes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from rgbctl.color import Color

CC_RGBINIT = 0x01
CC_FULLOPEN = 0x02  # show the RGB/HSV editor, not just the swatches


class _CHOOSECOLORW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HWND),
        ("rgbResult", wintypes.DWORD),
        ("lpCustColors", ctypes.POINTER(wintypes.DWORD)),
        ("Flags", wintypes.DWORD),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
    ]


# The dialog's 16 "custom colors" slots; kept for the life of the process.
_custom_colors = (wintypes.DWORD * 16)()


def _to_colorref(c: Color) -> int:
    return c.r | (c.g << 8) | (c.b << 16)


def _from_colorref(ref: int) -> Color:
    return Color(ref & 0xFF, (ref >> 8) & 0xFF, (ref >> 16) & 0xFF)


def choose_color(initial: Color, owner: int | None = None) -> Color | None:
    """Show the dialog; returns the picked color, or None if cancelled.

    With an owner window handle the dialog is modal to that window.
    """
    cc = _CHOOSECOLORW()
    cc.lStructSize = ctypes.sizeof(cc)
    cc.hwndOwner = owner
    cc.rgbResult = _to_colorref(initial)
    cc.lpCustColors = _custom_colors
    cc.Flags = CC_RGBINIT | CC_FULLOPEN
    if not ctypes.windll.comdlg32.ChooseColorW(ctypes.byref(cc)):
        return None
    return _from_colorref(cc.rgbResult)
