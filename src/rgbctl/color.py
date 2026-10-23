from __future__ import annotations

from dataclasses import dataclass

NAMED_COLORS = {
    "off": (0, 0, 0),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "purple": (128, 0, 255),
    "orange": (255, 100, 0),
    "pink": (255, 50, 150),
}


@dataclass(frozen=True, slots=True)
class Color:
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        for channel in (self.r, self.g, self.b):
            if not 0 <= channel <= 255:
                raise ValueError(f"color channel out of range: {channel}")

    @classmethod
    def parse(cls, text: str) -> Color:
        """Parse 'red', '#ff0000', 'ff0000' or '255,0,0'."""
        value = text.strip().lower()
        if value in NAMED_COLORS:
            return cls(*NAMED_COLORS[value])
        if "," in value:
            parts = [int(p) for p in value.split(",")]
            if len(parts) != 3:
                raise ValueError(f"expected r,g,b: {text!r}")
            return cls(*parts)
        value = value.removeprefix("#")
        if len(value) == 6:
            return cls(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
        raise ValueError(f"unrecognized color: {text!r}")

    def __str__(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"
