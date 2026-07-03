"""High-level Session / Recording object representing an input tracking session."""

from __future__ import annotations

import json
import os
from typing import Any

from cursortrack.core.codec import decompress_tolerant
from cursortrack.core.events import (
    BUTTON_NAME,
    ButtonEvent,
    InputEvent,
    MoveEvent,
    ScrollEvent,
    TapEvent,
    iter_events_v2,
    iter_positions_v1,
)
from cursortrack.core.format import read_header


class Session:
    """Represents a loaded cursor tracking recording and provides programmatic APIs."""

    def __init__(
        self,
        header: dict[str, Any],
        events: list[InputEvent],
        file_path: str | None = None,
    ):
        self.header = header
        self.events = events
        self.file_path = file_path

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
    def load(cls, path: str) -> Session:
        """Load a session recording from a binary .ctrk/.curmov, .npy, or .jsonl file."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Session file not found: {path}")

        if path.endswith(".npy"):
            return cls.load_npy(path)
        elif path.endswith(".jsonl"):
            return cls.load_jsonl(path)
        else:
            return cls.load_binary(path)

    @classmethod
    def load_binary(cls, path: str) -> Session:
        """Load from a binary format file (.ctrk / .curmov)."""
        with open(path, "rb") as f:
            header, leftover = read_header(f)
            blob = leftover + f.read()

        body = decompress_tolerant(blob, header["codec"])

        events: list[InputEvent]
        if header["version"] == 1:
            events = list(iter_positions_v1(header["x0"], header["y0"], body))
        else:
            events = list(iter_events_v2(header["x0"], header["y0"], body))

        return cls(header, events, path)

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
