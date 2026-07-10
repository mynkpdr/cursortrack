"""High-level Session / Recording object representing an input tracking session."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any

from cursortrack.core.codec import (
    CODEC_NAME,
    DEFAULT_MAX_DECOMPRESSED_BYTES,
    DecompressionStatus,
    decompress_with_status,
)
from cursortrack.core.events import (
    BUTTON_ID,
    BUTTON_NAME,
    CAP_CLICK,
    CAP_SCROLL,
    DEFAULT_MAX_ABS_COORDINATE,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_FRAME,
    ButtonEvent,
    InputEvent,
    MoveEvent,
    ScrollEvent,
    TapEvent,
    decode_events_v2,
    decode_positions_v1,
)
from cursortrack.core.format import read_header
from cursortrack.core.layout import (
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    MonitorLayout,
    Rect,
    ScrollUnit,
)

DEFAULT_MAX_COMPRESSED_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class DecodeLimits:
    """Resource limits applied when loading an untrusted binary session."""

    max_compressed_bytes: int = DEFAULT_MAX_COMPRESSED_BYTES
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES
    max_events: int = DEFAULT_MAX_EVENTS
    max_frame: int = DEFAULT_MAX_FRAME
    max_abs_coordinate: int = DEFAULT_MAX_ABS_COORDINATE

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value < 1:
                raise ValueError(f"{name} must be positive.")


def _validate_binary_header(header: dict[str, Any]) -> None:
    codec = int(header["codec"])
    if codec not in CODEC_NAME:
        raise ValueError(f"Unknown codec ID {codec} in session header.")
    rate = int(header["rate"])
    if not 1 <= rate <= 65535:
        raise ValueError(f"Session sample rate must be 1..65535 Hz; got {rate}.")
    if int(header["scr_w"]) < 0 or int(header["scr_h"]) < 0:
        raise ValueError("Session screen dimensions cannot be negative.")
    if not math.isfinite(float(header["start"])):
        raise ValueError("Session start time must be finite.")


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{label} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a finite number.") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number.")
    return result


def _integer(value: Any, label: str) -> int:
    number = _finite_number(value, label)
    if not number.is_integer():
        raise ValueError(f"{label} must be an integer.")
    return int(number)


def _validate_interchange_metadata(
    rate: int,
    scr_w: int,
    scr_h: int,
    capture: int,
    source: str,
) -> None:
    if not 1 <= rate <= 65535:
        raise ValueError(f"{source}: rate must be 1..65535.")
    if scr_w < 0 or scr_h < 0:
        raise ValueError(f"{source}: screen dimensions cannot be negative.")
    if not 0 <= capture <= 255:
        raise ValueError(f"{source}: capture must fit in one byte.")


class Session:
    """Represents a loaded cursor tracking recording and provides programmatic APIs."""

    def __init__(
        self,
        header: dict[str, Any],
        events: list[InputEvent],
        file_path: str | None = None,
        truncated: bool = False,
        integrity: str | None = None,
    ):
        self.header = header
        self.events = events
        self.file_path = file_path
        self.integrity = integrity or ("truncated" if truncated else "complete")
        if self.integrity not in {"complete", "truncated", "corrupt-recovered"}:
            raise ValueError(f"Unknown session integrity state: {self.integrity}")
        self.truncated = truncated or self.integrity != "complete"

    @property
    def version(self) -> int:
        return int(self.header.get("version", 2))

    @property
    def codec(self) -> int:
        return int(self.header.get("codec", 0))

    @property
    def rate(self) -> int:
        return int(self.header.get("rate", 144))

    @property
    def screen_width(self) -> int:
        return int(self.header.get("scr_w", 0))

    @property
    def screen_height(self) -> int:
        return int(self.header.get("scr_h", 0))

    @property
    def start_time(self) -> float:
        return float(self.header.get("start", 0.0))

    @property
    def capture_mask(self) -> int:
        return int(self.header.get("capture", 1))

    @property
    def layout_metadata_sufficient(self) -> bool:
        """True when the session carries portable layout facts (v3+).

        v1/v2 only store primary/virtual width and height without origin, unit,
        or monitor topology, which is insufficient for safe cross-machine replay.
        """
        return self.version >= 3 and bool(self.header.get("desktop"))

    @property
    def button_state_valid(self) -> bool:
        """Whether button press/release pairs form a valid playback sequence."""
        pressed: set[str] = set()
        for event in self.events:
            if not isinstance(event, ButtonEvent):
                continue
            if event.pressed:
                if event.button in pressed:
                    return False
                pressed.add(event.button)
            else:
                if event.button not in pressed:
                    return False
                pressed.discard(event.button)
        return not pressed

    def source_layout(self) -> DesktopLayout:
        """Best-effort source desktop layout for playback negotiation.

        v3 sessions expose stored desktop metadata. v1/v2 synthesize a single
        primary monitor from ``scr_w``/``scr_h`` when both are positive, with an
        unknown coordinate unit; otherwise the layout is explicitly unknown.
        """
        desktop = self.header.get("desktop")
        if isinstance(desktop, DesktopLayout):
            return desktop

        width = self.screen_width
        height = self.screen_height
        if width > 0 and height > 0:
            bounds = Rect(0, 0, width, height)
            return DesktopLayout(
                known=True,
                coordinate_unit=CoordinateUnit.UNKNOWN,
                bounds=bounds,
                monitors=(MonitorLayout(id="legacy-primary", primary=True, bounds=bounds),),
            )
        return DesktopLayout.unknown()

    def source_capabilities(self) -> InputCapabilities:
        """Infer source input capabilities from metadata and observed events."""
        caps = self.header.get("input")
        if isinstance(caps, InputCapabilities):
            return caps

        buttons: list[str] = []
        seen: set[str] = set()
        has_scroll = False
        for event in self.events:
            if isinstance(event, ButtonEvent) and event.button not in seen:
                seen.add(event.button)
            elif isinstance(event, ScrollEvent):
                has_scroll = True
            elif isinstance(event, TapEvent) and "left" not in seen:
                seen.add("left")
        for name in ("left", "right", "middle", "x1", "x2"):
            if name in seen:
                buttons.append(name)

        # Capture mask is a request, not a guarantee; still useful as a hint.
        mask = self.capture_mask
        return InputCapabilities(
            coordinate_unit=CoordinateUnit.UNKNOWN,
            buttons=tuple(buttons),
            scroll_units=(ScrollUnit.WHEEL_DETENT,) if has_scroll or (mask & CAP_SCROLL) else (),
            precise_scroll=False,
            read_position=True,
            inject_position=True,
            inject_buttons=bool(buttons) or bool(mask & CAP_CLICK),
            inject_scroll=has_scroll or bool(mask & CAP_SCROLL),
            capture_buttons=bool(mask & CAP_CLICK),
            capture_scroll=bool(mask & CAP_SCROLL),
        )

    @classmethod
    def load(cls, path: str, limits: DecodeLimits | None = None) -> Session:
        """Load a session recording from a binary .ctrk/.curmov, .npy, or .jsonl file."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Session file not found: {path}")

        if path.endswith(".npy"):
            return cls.load_npy(path)
        elif path.endswith(".jsonl"):
            return cls.load_jsonl(path)
        else:
            return cls.load_binary(path, limits=limits)

    @classmethod
    def load_binary(cls, path: str, limits: DecodeLimits | None = None) -> Session:
        """Load from a binary format file (.ctrk / .curmov)."""
        active_limits = limits or DecodeLimits()
        with open(path, "rb") as f:
            header, leftover = read_header(f)
            _validate_binary_header(header)
            remaining = active_limits.max_compressed_bytes - len(leftover)
            blob = leftover + f.read(max(0, remaining) + 1)
        if len(blob) > active_limits.max_compressed_bytes:
            raise ValueError(
                "Session compressed body exceeds the configured limit of "
                f"{active_limits.max_compressed_bytes} bytes."
            )

        decompressed = decompress_with_status(
            blob,
            header["codec"],
            max_output_bytes=active_limits.max_decompressed_bytes,
        )
        body = decompressed.data

        events: list[InputEvent]
        if header["version"] == 1:
            events, event_truncated = decode_positions_v1(
                header["x0"],
                header["y0"],
                body,
                active_limits.max_events,
                active_limits.max_frame,
                active_limits.max_abs_coordinate,
            )
        else:
            events, event_truncated = decode_events_v2(
                header["x0"],
                header["y0"],
                body,
                active_limits.max_events,
                active_limits.max_frame,
                active_limits.max_abs_coordinate,
            )

        if decompressed.status is DecompressionStatus.CORRUPT_RECOVERED:
            integrity = "corrupt-recovered"
        elif decompressed.status is DecompressionStatus.TRUNCATED or event_truncated:
            integrity = "truncated"
        else:
            integrity = "complete"
        return cls(header, events, path, integrity=integrity)

    @classmethod
    def load_jsonl(cls, path: str) -> Session:
        """Load a session recording from a JSONL file."""
        events: list[InputEvent] = []
        start_time = 0.0
        # Defaults used only for files exported before rate/scr_w/scr_h/capture were
        # written into every row; genuine exports always carry their own values.
        rate = 144
        scr_w = 0
        scr_h = 0
        capture = 15
        first_event = True
        previous_time: float | None = None

        with open(path, encoding="utf-8") as f:
            for line_number, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                source = f"JSONL line {line_number}"
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"{source}: invalid JSON ({e.msg}).") from None
                if not isinstance(data, dict):
                    raise ValueError(f"{source}: each event must be a JSON object.")
                if len(events) >= DEFAULT_MAX_EVENTS:
                    raise ValueError(
                        f"{source}: event count exceeds the configured limit "
                        f"of {DEFAULT_MAX_EVENTS}."
                    )

                t = _finite_number(data.get("t"), f"{source} timestamp")
                x = _integer(data.get("x"), f"{source} x")
                y = _integer(data.get("y"), f"{source} y")
                if abs(x) > DEFAULT_MAX_ABS_COORDINATE or abs(y) > DEFAULT_MAX_ABS_COORDINATE:
                    raise ValueError(f"{source}: coordinate exceeds the supported range.")
                if previous_time is not None and t < previous_time:
                    raise ValueError(f"{source}: timestamps must be nondecreasing.")

                if first_event:
                    start_time = t
                    rate = _integer(data.get("rate", rate), f"{source} rate")
                    scr_w = _integer(data.get("scr_w", scr_w), f"{source} scr_w")
                    scr_h = _integer(data.get("scr_h", scr_h), f"{source} scr_h")
                    capture = _integer(data.get("capture", capture), f"{source} capture")
                    _validate_interchange_metadata(rate, scr_w, scr_h, capture, source)
                    first_event = False
                else:
                    expected_metadata = {
                        "rate": rate,
                        "scr_w": scr_w,
                        "scr_h": scr_h,
                        "capture": capture,
                    }
                    for key, expected in expected_metadata.items():
                        if key in data and _integer(data[key], f"{source} {key}") != expected:
                            raise ValueError(f"{source}: session metadata changed at {key}.")

                frame = round((t - start_time) * rate)
                if frame > DEFAULT_MAX_FRAME:
                    raise ValueError(f"{source}: frame exceeds the supported range.")
                etype = data.get("type", "move")

                if etype == "move":
                    events.append(MoveEvent(frame=frame, x=x, y=y))
                elif etype in ("down", "up"):
                    btn = data.get("button", "left")
                    if not isinstance(btn, str) or btn not in BUTTON_ID:
                        raise ValueError(f"{source}: unsupported button {btn!r}.")
                    events.append(
                        ButtonEvent(frame=frame, x=x, y=y, button=btn, pressed=(etype == "down"))
                    )
                elif etype == "scroll":
                    sdx = _integer(data.get("sdx", 0), f"{source} sdx")
                    sdy = _integer(data.get("sdy", 0), f"{source} sdy")
                    if (
                        abs(sdx) > DEFAULT_MAX_ABS_COORDINATE
                        or abs(sdy) > DEFAULT_MAX_ABS_COORDINATE
                    ):
                        raise ValueError(f"{source}: scroll delta exceeds the supported range.")
                    events.append(ScrollEvent(frame=frame, x=x, y=y, sdx=sdx, sdy=sdy))
                elif etype == "tap":
                    touch_id = _integer(data.get("touch_id", 0), f"{source} touch_id")
                    if touch_id < 0:
                        raise ValueError(f"{source}: touch_id cannot be negative.")
                    events.append(TapEvent(frame=frame, x=x, y=y, touch_id=touch_id))
                else:
                    raise ValueError(f"{source}: unknown event type {etype!r}.")
                previous_time = t

        header = {
            "version": 2,
            "codec": 0,
            "rate": rate,
            "scr_w": scr_w,
            "scr_h": scr_h,
            "start": start_time,
            "x0": events[0].x if events else 0,
            "y0": events[0].y if events else 0,
            "capture": capture,
        }
        return cls(header, events, path)

    @classmethod
    def load_npy(cls, path: str) -> Session:
        """Load a session recording from a NumPy .npy array file."""
        try:
            import numpy as np
        except ImportError:
            raise ImportError(
                "Loading a NumPy track requires numpy. Install it using 'pip install numpy'."
            )

        arr = np.load(path, allow_pickle=False)
        if arr.ndim != 2 or arr.shape[1] < 3:
            raise ValueError(
                f"Expected a 2D numpy array with at least 3 columns; got shape {arr.shape}"
            )
        if len(arr) > DEFAULT_MAX_EVENTS:
            raise ValueError(
                f"NumPy event count exceeds the configured limit of {DEFAULT_MAX_EVENTS}."
            )
        if not np.issubdtype(arr.dtype, np.number):
            raise ValueError(f"NumPy session values must be numeric; got dtype {arr.dtype}.")
        if not bool(np.isfinite(arr).all()):
            raise ValueError("NumPy session values must all be finite.")

        events: list[InputEvent] = []
        has_rows = len(arr) > 0
        n_cols = arr.shape[1]
        if 7 <= n_cols < 10:
            raise ValueError("NumPy session metadata is incomplete; expected columns 6-9.")
        start_time = _finite_number(arr[0][0], "NumPy row 0 timestamp") if has_rows else 0.0
        # Columns 6-9 (rate, scr_w, scr_h, capture) are only present in files exported
        # by export_to_npy() after it started writing session metadata into every row;
        # older exports fall back to the previous hardcoded defaults.
        rate = _integer(arr[0][6], "NumPy rate") if has_rows and n_cols >= 10 else 144
        scr_w = _integer(arr[0][7], "NumPy scr_w") if has_rows and n_cols >= 10 else 0
        scr_h = _integer(arr[0][8], "NumPy scr_h") if has_rows and n_cols >= 10 else 0
        capture = _integer(arr[0][9], "NumPy capture") if has_rows and n_cols >= 10 else 15
        _validate_interchange_metadata(rate, scr_w, scr_h, capture, "NumPy metadata")
        if has_rows and n_cols >= 10 and not bool(np.all(arr[:, 6:10] == arr[0, 6:10])):
            raise ValueError("NumPy session metadata must be constant on every row.")

        previous_time: float | None = None
        for row_number, row in enumerate(arr):
            source = f"NumPy row {row_number}"
            t = _finite_number(row[0], f"{source} timestamp")
            if previous_time is not None and t < previous_time:
                raise ValueError(f"{source}: timestamps must be nondecreasing.")
            frame = round((t - start_time) * rate)
            if frame > DEFAULT_MAX_FRAME:
                raise ValueError(f"{source}: frame exceeds the supported range.")
            x = _integer(row[1], f"{source} x")
            y = _integer(row[2], f"{source} y")
            if abs(x) > DEFAULT_MAX_ABS_COORDINATE or abs(y) > DEFAULT_MAX_ABS_COORDINATE:
                raise ValueError(f"{source}: coordinate exceeds the supported range.")
            tid = _integer(row[3], f"{source} event type") if n_cols > 3 else 0
            aux1 = _integer(row[4], f"{source} aux1") if n_cols > 4 else 0
            aux2 = _integer(row[5], f"{source} aux2") if n_cols > 5 else 0

            if tid == 0:  # MOVE
                events.append(MoveEvent(frame=frame, x=x, y=y))
            elif tid in (1, 2):  # DOWN, UP
                btn = BUTTON_NAME.get(aux1)
                if btn is None:
                    raise ValueError(f"{source}: unknown button id {aux1}.")
                events.append(ButtonEvent(frame=frame, x=x, y=y, button=btn, pressed=(tid == 1)))
            elif tid == 3:  # SCROLL
                if abs(aux1) > DEFAULT_MAX_ABS_COORDINATE or abs(aux2) > DEFAULT_MAX_ABS_COORDINATE:
                    raise ValueError(f"{source}: scroll delta exceeds the supported range.")
                events.append(ScrollEvent(frame=frame, x=x, y=y, sdx=aux1, sdy=aux2))
            elif tid == 4:  # TAP
                if aux1 < 0:
                    raise ValueError(f"{source}: touch id cannot be negative.")
                events.append(TapEvent(frame=frame, x=x, y=y, touch_id=aux1))
            else:
                raise ValueError(f"{source}: unknown event type id {tid}.")
            previous_time = t

        header = {
            "version": 2,
            "codec": 0,
            "rate": rate,
            "scr_w": scr_w,
            "scr_h": scr_h,
            "start": start_time,
            "x0": events[0].x if events else 0,
            "y0": events[0].y if events else 0,
            "capture": capture,
        }
        return cls(header, events, path)

    def to_dataframe(self) -> Any:
        """Convert the session events into a Pandas DataFrame.

        Requires pandas to be installed.

        Returns:
            pandas.DataFrame with columns: t, frame, type, x, y, button, sdx, sdy, touch_id
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "Pandas is required to export to a DataFrame. Install it using 'pip install pandas'."
            )

        rows = []
        for ev in self.events:
            d = ev.to_dict()
            # Calculate absolute timestamp
            d["t"] = self.start_time + ev.frame / self.rate
            rows.append(d)

        # Standard column order
        cols = ["t", "frame", "type", "x", "y", "button", "sdx", "sdy", "touch_id"]
        df = pd.DataFrame(rows)
        # Ensure all columns exist
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df[cols]
