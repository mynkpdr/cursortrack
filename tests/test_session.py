"""Tests for high-level Session and binary file round-trip encodings."""

from __future__ import annotations

import struct

from cursortrack.core.events import (
    ButtonEvent,
    InputEvent,
    MoveEvent,
    ScrollEvent,
    TapEvent,
)
from cursortrack.core.format import (
    HEADER_FMT_V1,
    MAGIC_V1,
    pack_header,
    read_header,
)
from cursortrack.core.session import Session


def test_header_serialization() -> None:
    """Verify modern v2 binary headers serialize and parse correct values."""
    header_bytes = pack_header(
        codec=1,
        rate=144,
        scr_w=1920,
        scr_h=1080,
        start=123456789.0,
        x0=100,
        y0=200,
        capture=15,
    )
    assert len(header_bytes) == 36

    # Parse back
    import io

    stream = io.BytesIO(header_bytes)
    header, leftover = read_header(stream)
    assert leftover == b""
    assert header["version"] == 2
    assert header["codec"] == 1
    assert header["rate"] == 144
    assert header["scr_w"] == 1920
    assert header["scr_h"] == 1080
    assert header["start"] == 123456789.0
    assert header["x0"] == 100
    assert header["y0"] == 200
    assert header["capture"] == 15


def test_legacy_v1_header_reading() -> None:
    """Verify legacy v1 binary headers parse correctly for backwards compatibility."""
    # Magic(8s) Codec(B) Rate(H) ScreenW(i) ScreenH(i) Start(d) x0(i) y0(i)
    raw_v1 = struct.pack(
        HEADER_FMT_V1,
        MAGIC_V1,
        0,  # codec raw
        60,  # rate
        0,  # scr_w
        0,  # scr_h
        987654321.0,  # start
        10,  # x0
        20,  # y0
    )
    # Plus append some body bytes that shouldn't be read as part of the header
    raw_stream = raw_v1 + b"body_bytes"

    import io

    stream = io.BytesIO(raw_stream)
    header, leftover = read_header(stream)
    assert header["version"] == 1
    assert header["codec"] == 0
    assert header["rate"] == 60
    assert header["start"] == 987654321.0
    assert header["x0"] == 10
    assert header["y0"] == 20
    assert leftover + stream.read() == b"body_bytes"


def test_to_dataframe_conversion() -> None:
    """Validate converting loaded session events to Pandas DataFrame."""
    events: list[InputEvent] = [
        MoveEvent(frame=0, x=500, y=500),
        MoveEvent(frame=1, x=505, y=505),
        ButtonEvent(frame=2, x=505, y=505, button="left", pressed=True),
        ScrollEvent(frame=3, x=505, y=505, sdx=0, sdy=-1),
        TapEvent(frame=4, x=510, y=510, touch_id=0),
    ]
    header = {
        "version": 2,
        "codec": 0,
        "rate": 100,
        "scr_w": 1920,
        "scr_h": 1080,
        "start": 1000.0,
        "x0": 500,
        "y0": 500,
        "capture": 15,
    }
    session = Session(header, events)

    try:
        import pandas as pd
    except ImportError:
        # Skip dataframe assertions if pandas isn't installed
        return

    df = session.to_dataframe()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 5
    assert list(df.columns) == [
        "t",
        "frame",
        "type",
        "x",
        "y",
        "button",
        "sdx",
        "sdy",
        "touch_id",
    ]

    # Verify times computed correct sequence offset
    assert df.loc[0, "t"] == 1000.0
    assert df.loc[1, "t"] == 1000.01
    assert df.loc[2, "t"] == 1000.02

    # Verify correct types mapped
    assert df.loc[0, "type"] == "move"
    assert df.loc[2, "type"] == "down"
    assert df.loc[2, "button"] == "left"
    assert df.loc[3, "type"] == "scroll"
    assert df.loc[3, "sdy"] == -1
    assert df.loc[4, "type"] == "tap"
    assert df.loc[4, "touch_id"] == 0
