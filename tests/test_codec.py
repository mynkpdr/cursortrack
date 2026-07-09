"""Tests for varint, zigzag, and compression codecs."""

from __future__ import annotations

import io
import sys

import pytest

from cursortrack.core.codec import (
    CODEC_RAW,
    CODEC_ZLIB,
    CODEC_ZSTD,
    CodecWriter,
    decompress_tolerant,
    read_svarint,
    read_uvarint,
    write_svarint,
    write_uvarint,
    zigzag_decode,
    zigzag_encode,
)


def test_zigzag_roundtrip() -> None:
    """Validate signed zigzag encoding mapped to unsigned varints is lossless."""
    test_values = [0, 1, -1, 2, -2, 1000, -1000, sys.maxsize, -sys.maxsize]
    for val in test_values:
        encoded = zigzag_encode(val)
        assert encoded >= 0
        decoded = zigzag_decode(encoded)
        assert decoded == val


def test_uvarint_roundtrip() -> None:
    """Validate unsigned varints serialization and deserialization."""
    test_values = [0, 1, 127, 128, 255, 10000, 16383, 16384, 1000000]
    for val in test_values:
        buf = bytearray()
        write_uvarint(buf, val)
        assert len(buf) > 0
        decoded, pos, ok = read_uvarint(buf, 0)
        assert ok
        assert decoded == val
        assert pos == len(buf)


def test_svarint_roundtrip() -> None:
    """Validate signed varint serialization (zigzag + varint)."""
    test_values = [0, -1, 1, -127, 128, -128, 10000, -16384]
    for val in test_values:
        buf = bytearray()
        write_svarint(buf, val)
        decoded, pos, ok = read_svarint(buf, 0)
        assert ok
        assert decoded == val
        assert pos == len(buf)


def test_write_uvarint_rejects_negative_input() -> None:
    """Negative input must raise instead of looping forever appending bytes.

    Regression test: >>= on a negative Python int converges to -1 rather than
    0, so the encoder loop never terminated and grew the buffer unboundedly.
    """
    buf = bytearray()
    with pytest.raises(ValueError, match="non-negative"):
        write_uvarint(buf, -1)
    assert buf == bytearray()  # nothing partially appended


def test_truncated_varint() -> None:
    """Ensure truncated varint reads are detected as invalid."""
    buf = bytearray([0x80, 0x80])  # Continuation flag set but buffer ends
    _, _, ok = read_uvarint(buf, 0)
    assert not ok


def test_compression_writer_and_tolerant_decompress() -> None:
    """Ensure raw and zlib compressor streams can roundtrip successfully."""
    codecs = [CODEC_RAW, CODEC_ZLIB]
    try:
        import zstandard  # noqa: F401

        codecs.append(CODEC_ZSTD)
    except ImportError:
        pass

    test_data = b"Hello, CursorTrack testing data payload! " * 50

    for codec in codecs:
        f = io.BytesIO()
        writer = CodecWriter(f, codec, level=1 if codec == CODEC_ZLIB else 3)
        writer.write(test_data)
        writer.close()

        blob = f.getvalue()
        decompressed = decompress_tolerant(blob, codec)
        assert decompressed == test_data


def test_decompress_unfinalized_zlib() -> None:
    """Ensure the zlib tolerant decompressor recovers bytes from unfinalized streams."""
    f = io.BytesIO()
    writer = CodecWriter(f, CODEC_ZLIB, level=6)
    writer.write(b"chunk1")
    writer.flush()
    # Read without closing (unfinalized, no finish frame)
    blob = f.getvalue()
    # Should decompress what was written before closing/finishing
    decompressed = decompress_tolerant(blob, CODEC_ZLIB)
    assert decompressed == b"chunk1"
    writer.close()
