"""Event dataclasses, enums, and encoding/decoding implementations."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum

from cursortrack.core.codec import (
    read_svarint,
    read_uvarint,
    write_svarint,
    write_uvarint,
    zigzag_decode,
)

# Capture bitmasks
CAP_MOVE = 1
CAP_CLICK = 2
CAP_SCROLL = 4
CAP_TOUCH = 8
CAP_ALL = CAP_MOVE | CAP_CLICK | CAP_SCROLL | CAP_TOUCH

CAP_NAME: dict[int, str] = {
    CAP_MOVE: "move",
    CAP_CLICK: "click",
    CAP_SCROLL: "scroll",
    CAP_TOUCH: "touch",
}


class EventTag(IntEnum):
    MOVE = 0
    DOWN = 1
    UP = 2
    SCROLL = 3
    TAP = 4


BUTTON_NAME: dict[int, str] = {0: "left", 1: "right", 2: "middle", 3: "x1", 4: "x2"}
BUTTON_ID: dict[str, int] = {v: k for k, v in BUTTON_NAME.items()}


@dataclass
class InputEvent:
    """Base class for all input events."""

    frame: int
    x: int
    y: int

    def to_dict(self) -> dict[str, int | float | str]:
        """Convert event to a standard dict representation."""
        return {
            "frame": self.frame,
            "x": self.x,
            "y": self.y,
        }


@dataclass
class MoveEvent(InputEvent):
    """Cursor movement event."""

    def to_dict(self) -> dict[str, int | float | str]:
        d = super().to_dict()
        d["type"] = "move"
        return d


@dataclass
class ButtonEvent(InputEvent):
    """Mouse button press (down) or release (up) event."""

    button: str
    pressed: bool

    def to_dict(self) -> dict[str, int | float | str]:
        d = super().to_dict()
        d["type"] = "down" if self.pressed else "up"
        d["button"] = self.button
        return d


@dataclass
class ScrollEvent(InputEvent):
    """Mouse scroll event (wheel/trackpad)."""

    sdx: int
    sdy: int

    def to_dict(self) -> dict[str, int | float | str]:
        d = super().to_dict()
        d["type"] = "scroll"
        d["sdx"] = self.sdx
        d["sdy"] = self.sdy
        return d


@dataclass
class TapEvent(InputEvent):
    """Touchpad/screen tap event."""

    touch_id: int

    def to_dict(self) -> dict[str, int | float | str]:
        d = super().to_dict()
        d["type"] = "tap"
        d["touch_id"] = self.touch_id
        return d


def encode_move(buf: bytearray, dframes: int, dx: int, dy: int) -> None:
    """Encode move tag and its fields."""
    write_uvarint(buf, EventTag.MOVE)
    write_uvarint(buf, dframes)
    write_svarint(buf, dx)
    write_svarint(buf, dy)


def encode_click(
    buf: bytearray, dframes: int, is_down: bool, button: int, dx: int, dy: int
) -> None:
    """Encode down or up tag and its fields."""
    tag = EventTag.DOWN if is_down else EventTag.UP
    write_uvarint(buf, tag)
    write_uvarint(buf, dframes)
    write_uvarint(buf, button)
    write_svarint(buf, dx)
    write_svarint(buf, dy)


def encode_scroll(buf: bytearray, dframes: int, sdx: int, sdy: int, dx: int, dy: int) -> None:
    """Encode scroll tag and its fields."""
    write_uvarint(buf, EventTag.SCROLL)
    write_uvarint(buf, dframes)
    write_svarint(buf, sdx)
    write_svarint(buf, sdy)
    write_svarint(buf, dx)
    write_svarint(buf, dy)


def encode_tap(buf: bytearray, dframes: int, touch_id: int, dx: int, dy: int) -> None:
    """Encode tap tag and its fields."""
    write_uvarint(buf, EventTag.TAP)
    write_uvarint(buf, dframes)
    write_uvarint(buf, touch_id)
    write_svarint(buf, dx)
    write_svarint(buf, dy)


def iter_events_v2(x0: int, y0: int, body: bytes) -> Iterator[InputEvent]:
    """Yield typed InputEvent objects from a v2 binary stream body."""
    pos = 0
    n = len(body)
    x, y = x0, y0
    frame = 0
    yield MoveEvent(frame=0, x=x, y=y)

    while pos < n:
        tag, pos, ok = read_uvarint(body, pos)
        if not ok:
            break
        dframes, pos, ok = read_uvarint(body, pos)
        if not ok:
            break
        frame += dframes

        if tag == EventTag.MOVE:
            dx, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            dy, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            x += dx
            y += dy
            yield MoveEvent(frame=frame, x=x, y=y)

        elif tag in (EventTag.DOWN, EventTag.UP):
            button, pos, ok = read_uvarint(body, pos)
            if not ok:
                break
            dx, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            dy, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            x += dx
            y += dy
            btn_name = BUTTON_NAME.get(button, f"button{button}")
            yield ButtonEvent(
                frame=frame, x=x, y=y, button=btn_name, pressed=(tag == EventTag.DOWN)
            )

        elif tag == EventTag.SCROLL:
            sdx, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            sdy, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            dx, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            dy, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            x += dx
            y += dy
            yield ScrollEvent(frame=frame, x=x, y=y, sdx=sdx, sdy=sdy)

        elif tag == EventTag.TAP:
            touch_id, pos, ok = read_uvarint(body, pos)
            if not ok:
                break
            dx, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            dy, pos, ok = read_svarint(body, pos)
            if not ok:
                break
            x += dx
            y += dy
            yield TapEvent(frame=frame, x=x, y=y, touch_id=touch_id)
        else:
            # Unknown tag - stop parsing to prevent corruption propagation
            break


def iter_positions_v1(x0: int, y0: int, body: bytes) -> Iterator[MoveEvent]:
    """Yield MoveEvent objects from a legacy v1 (move-only) binary stream body."""
    yield MoveEvent(frame=0, x=x0, y=y0)
    pos = 0
    x, y = x0, y0
    n = len(body)
    frame = 0
    while pos < n:
        u, pos, ok = read_uvarint(body, pos)
        if not ok:
            break
        v, pos, ok = read_uvarint(body, pos)
        if not ok:
            break
        x += zigzag_decode(u)
        y += zigzag_decode(v)
        frame += 1
        yield MoveEvent(frame=frame, x=x, y=y)
