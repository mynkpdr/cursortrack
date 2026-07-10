"""Independent structural verification for accepted v3 golden vectors."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from cursortrack.core.codec import read_svarint, read_uvarint

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v3"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _read_uvarint(data: bytes, pos: int, end: int) -> tuple[int, int]:
    value, new_pos, ok = read_uvarint(data, pos)
    if not ok or new_pos > end:
        raise AssertionError("Truncated fixture uvarint")
    return value, new_pos


def _read_svarint(data: bytes, pos: int, end: int) -> tuple[int, int]:
    value, new_pos, ok = read_svarint(data, pos)
    if not ok or new_pos > end:
        raise AssertionError("Truncated fixture svarint")
    return value, new_pos


def _verify_event(data: bytes, pos: int) -> tuple[int, int, int, int]:
    record_length, body_pos = _read_uvarint(data, pos, len(data))
    end = body_pos + record_length
    assert end <= len(data)

    tag, body_pos = _read_uvarint(data, body_pos, end)
    delta_us, body_pos = _read_uvarint(data, body_pos, end)
    dx, body_pos = _read_svarint(data, body_pos, end)
    dy, body_pos = _read_svarint(data, body_pos, end)

    if tag in (1, 2, 4):
        _, body_pos = _read_uvarint(data, body_pos, end)
    elif tag == 3:
        _, body_pos = _read_svarint(data, body_pos, end)
        _, body_pos = _read_svarint(data, body_pos, end)
    else:
        assert tag == 0
    assert body_pos == end
    return end, delta_us, dx, dy


def _verify_fixture(name: str, expected: dict[str, Any]) -> None:
    assert Path(expected["file"]).stem == name
    data = bytes.fromhex((FIXTURE_DIR / expected["file"]).read_text(encoding="ascii"))
    assert len(data) == expected["bytes"]
    assert hashlib.sha256(data).hexdigest() == expected["sha256"]

    magic, flags, codec, reserved, metadata_length, metadata_crc = struct.unpack_from(
        "<8sHBBII", data
    )
    assert magic == b"CURMOV03"
    assert (flags, codec, reserved) == (0, 0, 0)

    pos = 20
    metadata_bytes = data[pos : pos + metadata_length]
    assert zlib.crc32(metadata_bytes) & 0xFFFFFFFF == metadata_crc
    metadata = json.loads(metadata_bytes)
    canonical = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert metadata_bytes == canonical
    assert metadata["schema"] == 3
    assert [metadata["initial_pointer"]["x"], metadata["initial_pointer"]["y"]] == expected[
        "initial_pointer"
    ]
    x, y = expected["initial_pointer"]
    pos += metadata_length

    chunks = 0
    events = 0
    duration_us = 0
    while data[pos : pos + 4] == b"CTCH":
        marker, sequence, compressed_length, raw_length, event_count, raw_crc = struct.unpack_from(
            "<4sIIIII", data, pos
        )
        assert marker == b"CTCH"
        assert sequence == chunks
        assert compressed_length == raw_length
        pos += 24
        payload = data[pos : pos + compressed_length]
        assert len(payload) == raw_length
        assert zlib.crc32(payload) & 0xFFFFFFFF == raw_crc

        event_pos = 0
        for _ in range(event_count):
            event_pos, delta, dx, dy = _verify_event(payload, event_pos)
            duration_us += delta
            x += dx
            y += dy
            assert -(1 << 31) <= x <= (1 << 31) - 1
            assert -(1 << 31) <= y <= (1 << 31) - 1
        assert event_pos == len(payload)
        events += event_count
        chunks += 1
        pos += compressed_length

    footer = data[pos:]
    assert len(footer) == 32
    marker, footer_chunks, footer_events, footer_duration, footer_crc = struct.unpack(
        "<8sIQQI", footer
    )
    assert marker == b"CTEND03\0"
    assert zlib.crc32(footer[:28]) & 0xFFFFFFFF == footer_crc
    assert (footer_chunks, footer_events, footer_duration) == (
        chunks,
        events,
        duration_us,
    )
    assert (chunks, events, duration_us) == (
        expected["chunks"],
        expected["events"],
        expected["duration_us"],
    )


@pytest.mark.parametrize(("name", "expected"), MANIFEST["fixtures"].items())
def test_v3_golden_fixture(name: str, expected: dict[str, Any]) -> None:
    _verify_fixture(name, expected)
