"""Binary header serialization and format parsing."""

from __future__ import annotations

import struct
from typing import Any, BinaryIO

MAGIC_V1 = b"CURMOV01"
MAGIC_V2 = b"CURMOV02"

HEADER_FMT_V2 = "<8sBHiidiiB"
HEADER_SIZE_V2 = struct.calcsize(HEADER_FMT_V2)

HEADER_FMT_V1 = "<8sBHiidii"
HEADER_SIZE_V1 = struct.calcsize(HEADER_FMT_V1)


def pack_header(
    codec: int,
    rate: int,
    scr_w: int,
    scr_h: int,
    start: float,
    x0: int,
    y0: int,
    capture: int,
) -> bytes:
    """Pack metadata into a v2 cursortrack binary header."""
    return struct.pack(
        HEADER_FMT_V2,
        MAGIC_V2,
        codec,
        rate,
        scr_w,
        scr_h,
        start,
        x0,
        y0,
        capture,
    )


def read_header(f: BinaryIO) -> tuple[dict[str, Any], bytes]:
    """Read and parse either a legacy v1 or modern v2 header from the stream.

    Returns:
        A tuple of (header_metadata_dict, leftover_bytes). Leftover bytes
        occur if we read more bytes than the header version required.
    """
    raw = f.read(HEADER_SIZE_V2)
    if len(raw) >= 8 and raw[:8] == MAGIC_V1:
        # Legacy v1 header is shorter and lacks the capture field
        if len(raw) < HEADER_SIZE_V1:
            raise ValueError("File is truncated / not a valid curmov file.")
        v1_data = raw[:HEADER_SIZE_V1]
        magic, codec, rate, scr_w, scr_h, start, x0, y0 = struct.unpack(HEADER_FMT_V1, v1_data)
        leftover = raw[HEADER_SIZE_V1:]
        header = {
            "version": 1,
            "codec": codec,
            "rate": rate,
            "scr_w": scr_w,
            "scr_h": scr_h,
            "start": start,
            "x0": x0,
            "y0": y0,
            "capture": 1,  # v1 was always movement capture only
        }
        return header, leftover

    if len(raw) < HEADER_SIZE_V2:
        raise ValueError("File too short / not a valid cursortrack file.")

    magic, codec, rate, scr_w, scr_h, start, x0, y0, capture = struct.unpack(HEADER_FMT_V2, raw)
    if magic != MAGIC_V2:
        raise ValueError("Bad magic - not a valid cursortrack file.")

    header = {
        "version": 2,
        "codec": codec,
        "rate": rate,
        "scr_w": scr_w,
        "scr_h": scr_h,
        "start": start,
        "x0": x0,
        "y0": y0,
        "capture": capture,
    }
    return header, b""
