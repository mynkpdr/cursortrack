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
    BUTTON_NAME,
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

        with open(path, encoding="utf-8") as f:
            for i, raw_line in enumerate(f):
                line = raw_line.strip()
                if not line:
                    continue
                data = json.loads(line)
                t = float(data.get("t", 0.0))
                if i == 0:
                    start_time = t
                    rate = int(data.get("rate", rate))
                    scr_w = int(data.get("scr_w", scr_w))
                    scr_h = int(data.get("scr_h", scr_h))
                    capture = int(data.get("capture", capture))

                frame = round((t - start_time) * rate)
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                etype = data.get("type", "move")

                if etype == "move":
                    events.append(MoveEvent(frame=frame, x=x, y=y))
                elif etype in ("down", "up"):
                    btn = data.get("button", "left")
                    events.append(
                        ButtonEvent(frame=frame, x=x, y=y, button=btn, pressed=(etype == "down"))
                    )
                elif etype == "scroll":
                    sdx = int(data.get("sdx", 0))
                    sdy = int(data.get("sdy", 0))
                    events.append(ScrollEvent(frame=frame, x=x, y=y, sdx=sdx, sdy=sdy))
                elif etype == "tap":
                    touch_id = int(data.get("touch_id", 0))
                    events.append(TapEvent(frame=frame, x=x, y=y, touch_id=touch_id))

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

        arr = np.load(path)
        if arr.ndim != 2 or arr.shape[1] < 3:
            raise ValueError(
                f"Expected a 2D numpy array with at least 3 columns; got shape {arr.shape}"
            )

        events: list[InputEvent] = []
        has_rows = len(arr) > 0
        n_cols = arr.shape[1]
        start_time = float(arr[0][0]) if has_rows else 0.0
        # Columns 6-9 (rate, scr_w, scr_h, capture) are only present in files exported
        # by export_to_npy() after it started writing session metadata into every row;
        # older exports fall back to the previous hardcoded defaults.
        rate = int(arr[0][6]) if has_rows and n_cols > 6 else 144
        scr_w = int(arr[0][7]) if has_rows and n_cols > 7 else 0
        scr_h = int(arr[0][8]) if has_rows and n_cols > 8 else 0
        capture = int(arr[0][9]) if has_rows and n_cols > 9 else 15

        for row in arr:
            t = float(row[0])
            frame = round((t - start_time) * rate)
            x = int(row[1])
            y = int(row[2])
            tid = int(row[3]) if arr.shape[1] > 3 else 0
            aux1 = float(row[4]) if arr.shape[1] > 4 else 0.0
            aux2 = float(row[5]) if arr.shape[1] > 5 else 0.0

            if tid == 0:  # MOVE
                events.append(MoveEvent(frame=frame, x=x, y=y))
            elif tid in (1, 2):  # DOWN, UP
                btn = BUTTON_NAME.get(int(aux1), "left")
                events.append(ButtonEvent(frame=frame, x=x, y=y, button=btn, pressed=(tid == 1)))
            elif tid == 3:  # SCROLL
                events.append(ScrollEvent(frame=frame, x=x, y=y, sdx=int(aux1), sdy=int(aux2)))
            elif tid == 4:  # TAP
                events.append(TapEvent(frame=frame, x=x, y=y, touch_id=int(aux1)))

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
