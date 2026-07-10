"""Tests for high-level Session and binary file round-trip encodings."""

from __future__ import annotations

import io
import struct

import pytest

from cursortrack.core.codec import CODEC_RAW, CODEC_ZLIB, CodecWriter, write_uvarint
from cursortrack.core.events import (
    ButtonEvent,
    InputEvent,
    MoveEvent,
    ScrollEvent,
    TapEvent,
    decode_events_v2,
    encode_click,
    encode_move,
)
from cursortrack.core.format import (
    HEADER_FMT_V1,
    MAGIC_V1,
    pack_header,
    read_header,
)
from cursortrack.core.session import DecodeLimits, Session


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


def _write_v2_file(path: str, body: bytes) -> None:
    """Write a minimal raw-codec v2 binary file (header + uncompressed body)."""
    header = pack_header(
        codec=CODEC_RAW, rate=144, scr_w=1920, scr_h=1080, start=1000.0, x0=100, y0=200, capture=15
    )
    with open(path, "wb") as f:
        f.write(header + body)


def test_load_binary_intact_roundtrip_is_not_truncated(tmp_path) -> None:
    """A clean, fully-decodable body must report truncated=False."""
    buf = bytearray()
    encode_move(buf, 1, 5, 5)
    encode_click(buf, 1, True, 0, 0, 0)
    path = tmp_path / "intact.ctrk"
    _write_v2_file(str(path), bytes(buf))

    session = Session.load(str(path))
    assert session.truncated is False
    assert len(session.events) == 3  # implicit frame-0 move + the two encoded events


def test_load_binary_truncated_file_sets_session_flag(tmp_path) -> None:
    """End-to-end: Session.load on a half-written file must surface truncated=True."""
    buf = bytearray()
    encode_move(buf, 1, 5, 5)
    buf.append(0x80)  # incomplete trailing varint, as if the writer was killed mid-flush
    path = tmp_path / "half_recovered.ctrk"
    _write_v2_file(str(path), bytes(buf))

    session = Session.load(str(path))
    assert session.truncated is True
    assert len(session.events) == 2  # implicit frame-0 move + the one complete move


def test_load_binary_mid_varint_truncation_keeps_partial_events() -> None:
    """A body cut off mid-varint must decode everything before it and flag truncated."""
    buf = bytearray()
    encode_move(buf, 1, 5, 5)
    encode_move(buf, 1, 10, 10)
    buf.append(0x80)  # continuation bit set, but no terminating byte follows

    events, truncated = decode_events_v2(100, 200, bytes(buf))
    assert truncated is True
    assert len(events) == 3  # implicit frame-0 move + the two complete moves


def test_load_binary_unknown_tag_truncation_stops_and_flags() -> None:
    """An unrecognized tag must stop decoding (ignoring any trailing bytes) and flag truncated."""
    buf = bytearray()
    encode_move(buf, 1, 5, 5)
    write_uvarint(buf, 99)  # unknown tag
    write_uvarint(buf, 1)  # dframes
    buf += b"\x01\x02\x03"  # trailing bytes that must never be consumed as a new event

    events, truncated = decode_events_v2(100, 200, bytes(buf))
    assert truncated is True
    assert len(events) == 2  # implicit frame-0 move + the one complete move


def test_session_default_truncated_is_false() -> None:
    """Constructing a Session directly (no decoder involved) must default to untruncated."""
    header = {
        "version": 2,
        "codec": 0,
        "rate": 100,
        "scr_w": 1920,
        "scr_h": 1080,
        "start": 1000.0,
        "x0": 0,
        "y0": 0,
        "capture": 15,
    }
    session = Session(header, [MoveEvent(frame=0, x=0, y=0)])
    assert session.truncated is False
    assert session.integrity == "complete"


def test_unfinalized_compression_marks_session_truncated_at_event_boundary(
    tmp_path: object,
) -> None:
    """Compression truncation must survive even when all recovered events decode cleanly."""
    body = bytearray()
    encode_move(body, 1, 5, 5)

    compressed = io.BytesIO()
    writer = CodecWriter(compressed, CODEC_ZLIB, level=6)
    writer.write(bytes(body))
    writer.flush()

    path = str(tmp_path) + "/unfinalized.ctrk"
    header = pack_header(
        codec=CODEC_ZLIB,
        rate=144,
        scr_w=1920,
        scr_h=1080,
        start=1000.0,
        x0=100,
        y0=200,
        capture=15,
    )
    with open(path, "wb") as f:
        f.write(header + compressed.getvalue())
    writer.close()

    session = Session.load(path)

    assert len(session.events) == 2
    assert session.truncated is True
    assert session.integrity == "truncated"


def test_binary_load_enforces_compressed_size_limit(tmp_path: object) -> None:
    path = str(tmp_path) + "/oversized.ctrk"
    _write_v2_file(path, b"\x00\x00\x00")

    with pytest.raises(ValueError, match="compressed body exceeds"):
        Session.load_binary(
            path,
            limits=DecodeLimits(max_compressed_bytes=2),
        )


def test_binary_load_enforces_event_count_limit(tmp_path: object) -> None:
    body = bytearray()
    encode_move(body, 1, 1, 1)
    encode_move(body, 1, 1, 1)
    path = str(tmp_path) + "/too-many-events.ctrk"
    _write_v2_file(path, bytes(body))

    with pytest.raises(ValueError, match="event count exceeds"):
        Session.load_binary(path, limits=DecodeLimits(max_events=2))


def test_binary_load_allows_exact_event_count_limit(tmp_path: object) -> None:
    body = bytearray()
    encode_move(body, 1, 1, 1)
    path = str(tmp_path) + "/exact-event-limit.ctrk"
    _write_v2_file(path, bytes(body))

    session = Session.load_binary(path, limits=DecodeLimits(max_events=2))

    assert len(session.events) == 2


def test_binary_load_enforces_frame_and_coordinate_limits(tmp_path: object) -> None:
    frame_body = bytearray()
    encode_move(frame_body, 11, 0, 0)
    frame_path = str(tmp_path) + "/frame-limit.ctrk"
    _write_v2_file(frame_path, bytes(frame_body))

    with pytest.raises(ValueError, match="frame exceeds"):
        Session.load_binary(frame_path, limits=DecodeLimits(max_frame=10))

    coordinate_body = bytearray()
    encode_move(coordinate_body, 1, 11, 0)
    coordinate_path = str(tmp_path) + "/coordinate-limit.ctrk"
    _write_v2_file(coordinate_path, bytes(coordinate_body))

    with pytest.raises(ValueError, match="coordinate exceeds"):
        Session.load_binary(
            coordinate_path,
            limits=DecodeLimits(max_abs_coordinate=10),
        )


def test_binary_load_rejects_invalid_header_values(tmp_path: object) -> None:
    path = str(tmp_path) + "/invalid-rate.ctrk"
    header = pack_header(
        codec=CODEC_RAW,
        rate=0,
        scr_w=1920,
        scr_h=1080,
        start=1000.0,
        x0=0,
        y0=0,
        capture=1,
    )
    with open(path, "wb") as f:
        f.write(header)

    with pytest.raises(ValueError, match="sample rate"):
        Session.load_binary(path)
