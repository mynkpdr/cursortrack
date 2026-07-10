"""Varint, zigzag, and streaming codec compression abstraction."""

from __future__ import annotations

import contextlib
import os
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO, Protocol

# Codec constants
CODEC_RAW = 0
CODEC_ZSTD = 1
CODEC_ZLIB = 2

CODEC_NAME: dict[int, str] = {
    CODEC_RAW: "raw",
    CODEC_ZSTD: "zstd",
    CODEC_ZLIB: "zlib",
}

MAX_UVARINT_BYTES = 10
MAX_UVARINT = (1 << 64) - 1
DEFAULT_MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024


class DecompressionStatus(str, Enum):
    """Integrity state of a decoded compression stream."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    CORRUPT_RECOVERED = "corrupt-recovered"


@dataclass(frozen=True)
class DecompressionResult:
    """Recovered bytes and the compression stream's integrity state."""

    data: bytes
    status: DecompressionStatus


class _ZlibDecompressor(Protocol):
    @property
    def unconsumed_tail(self) -> bytes:
        """Compressed input not consumed because an output limit was reached."""
        ...

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        """Return decompressed bytes while retaining streaming state."""
        ...


def zigzag_encode(n: int) -> int:
    """Map signed -> unsigned so small magnitudes stay small (0,-1,1,-2 -> 0,1,2,3)."""
    return (abs(n) << 1) - (1 if n < 0 else 0)


def zigzag_decode(u: int) -> int:
    """Map unsigned back to signed (0,1,2,3 -> 0,-1,1,-2)."""
    return (u >> 1) ^ -(u & 1)


def write_uvarint(buf: bytearray, u: int) -> None:
    """Encode an unsigned integer into varint representation and append to buf."""
    if u < 0:
        # Without this guard the loop never terminates: >> on a negative int
        # converges to -1, appending continuation bytes forever.
        raise ValueError(
            f"write_uvarint requires a non-negative integer (got {u}); "
            "use write_svarint for signed values."
        )
    if u > MAX_UVARINT:
        raise ValueError(f"write_uvarint value exceeds the uint64 range: {u}")
    while True:
        b = u & 0x7F
        u >>= 7
        if u:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return


def read_uvarint(buf: bytes | bytearray, pos: int) -> tuple[int, int, bool]:
    """Read an unsigned varint from buf starting at pos.

    Returns:
        (value, new_pos, ok) where ok=False means a truncated trailing varint.
    """
    result = 0
    n = len(buf)
    for byte_index in range(MAX_UVARINT_BYTES):
        if pos >= n:
            return 0, pos, False
        b = buf[pos]
        pos += 1
        if byte_index == MAX_UVARINT_BYTES - 1 and b > 1:
            raise ValueError(
                f"Unsigned varint exceeds {MAX_UVARINT_BYTES} bytes or the uint64 range."
            )
        result |= (b & 0x7F) << (byte_index * 7)
        if not (b & 0x80):
            return result, pos, True
    raise ValueError(f"Unsigned varint exceeds {MAX_UVARINT_BYTES} bytes.")


def write_svarint(buf: bytearray, n: int) -> None:
    """Encode a signed integer (zigzag + varint) and append to buf."""
    write_uvarint(buf, zigzag_encode(n))


def read_svarint(buf: bytes | bytearray, pos: int) -> tuple[int, int, bool]:
    """Read a signed varint (zigzag + varint) from buf starting at pos.

    Returns:
        (value, new_pos, ok) where ok=False means a truncated trailing varint.
    """
    u, pos, ok = read_uvarint(buf, pos)
    return zigzag_decode(u), pos, ok


class CodecWriter:
    """Streaming compressor that supports crash-safe syncing to disk."""

    def __init__(self, f: BinaryIO, codec: int, level: int):
        self.f = f
        self.codec = codec
        self._co = None
        self._zstd = None
        self._w = None

        if codec == CODEC_ZSTD:
            try:
                import zstandard as zstd

                self._zstd = zstd
                self._c = zstd.ZstdCompressor(level=level)
                self._w = self._c.stream_writer(f, closefd=False)
            except ImportError:
                raise ImportError("zstd requested but 'zstandard' is not installed.")
        elif codec == CODEC_ZLIB:
            self._co = zlib.compressobj(level)

    def write(self, data: bytes) -> None:
        """Write compressed data to the underlying file stream."""
        if self.codec == CODEC_ZSTD:
            if self._w is not None:
                self._w.write(data)
        elif self.codec == CODEC_ZLIB:
            if self._co is not None:
                self.f.write(self._co.compress(data))
        else:
            self.f.write(data)

    def flush(self) -> None:
        """Emit a decodable boundary and push bytes all the way to the disk platter."""
        if self.codec == CODEC_ZSTD:
            if self._w is not None and self._zstd is not None:
                self._w.flush(self._zstd.FLUSH_BLOCK)
        elif self.codec == CODEC_ZLIB and self._co is not None:
            self.f.write(self._co.flush(zlib.Z_SYNC_FLUSH))
        self.f.flush()
        with contextlib.suppress(OSError, ValueError):
            os.fsync(self.f.fileno())

    def close(self) -> None:
        """Finalize compression frames and flush any remaining bytes."""
        if self.codec == CODEC_ZSTD:
            if self._w is not None:
                # close() finalizes the active frame. Flushing FLUSH_FRAME first
                # makes close() append a second frame that decoders treat as a tail.
                self._w.close()  # type: ignore[no-untyped-call]
        elif self.codec == CODEC_ZLIB and self._co is not None:
            self.f.write(self._co.flush(zlib.Z_FINISH))
        self.f.flush()
        with contextlib.suppress(OSError, ValueError):
            os.fsync(self.f.fileno())


def _check_output_limit(size: int, max_output_bytes: int) -> None:
    if size > max_output_bytes:
        raise ValueError(
            f"Decompressed data exceeds the configured limit of {max_output_bytes} bytes."
        )


def decompress_with_status(
    blob: bytes,
    codec: int,
    max_output_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
) -> DecompressionResult:
    """Recover a compression stream prefix while reporting its integrity."""
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be positive.")

    if codec == CODEC_RAW:
        _check_output_limit(len(blob), max_output_bytes)
        return DecompressionResult(blob, DecompressionStatus.COMPLETE)
    if codec == CODEC_ZSTD:
        try:
            import zstandard as zstd

            zstd_out = bytearray()
            decompressor = zstd.ZstdDecompressor().decompressobj()
            status = DecompressionStatus.COMPLETE
            try:
                for offset in range(0, len(blob), 1 << 16):
                    zstd_out += decompressor.decompress(blob[offset : offset + (1 << 16)])
                    _check_output_limit(len(zstd_out), max_output_bytes)
                zstd_out += decompressor.flush()
                _check_output_limit(len(zstd_out), max_output_bytes)
            except zstd.ZstdError:
                status = DecompressionStatus.CORRUPT_RECOVERED
            else:
                if decompressor.unused_data:
                    status = DecompressionStatus.CORRUPT_RECOVERED
                elif not decompressor.eof:
                    status = DecompressionStatus.TRUNCATED
            return DecompressionResult(bytes(zstd_out), status)
        except ImportError:
            # Fallback block in case zstd is missing on read (should be caught by validation,
            # but provide fallback for robustness).
            raise ImportError("zstd compression used but 'zstandard' is not installed.")
    if codec == CODEC_ZLIB:
        d = zlib.decompressobj()
        try:
            # Fast path: intact or merely truncated streams decode in one call
            # (truncation does not raise; it just ends the output early).
            zlib_out = d.decompress(blob, max_output_bytes + 1)
            _check_output_limit(len(zlib_out), max_output_bytes)
            if d.unconsumed_tail:
                _check_output_limit(max_output_bytes + 1, max_output_bytes)
            if d.unused_data:
                status = DecompressionStatus.CORRUPT_RECOVERED
            else:
                status = DecompressionStatus.COMPLETE if d.eof else DecompressionStatus.TRUNCATED
            return DecompressionResult(zlib_out, status)
        except zlib.error:
            pass
        return DecompressionResult(
            _recover_corrupt_zlib(blob, max_output_bytes),
            DecompressionStatus.CORRUPT_RECOVERED,
        )
    raise ValueError(f"Unknown codec ID {codec}")


def decompress_tolerant(
    blob: bytes,
    codec: int,
    max_output_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
) -> bytes:
    """Decompress a possibly damaged stream, returning its valid prefix."""
    return decompress_with_status(blob, codec, max_output_bytes).data


def _decompress_zlib_chunk(
    decompressor: _ZlibDecompressor,
    data: bytes,
    produced: int,
    max_output_bytes: int,
) -> bytes:
    chunk = decompressor.decompress(data, max_output_bytes - produced + 1)
    _check_output_limit(produced + len(chunk), max_output_bytes)
    if decompressor.unconsumed_tail:
        _check_output_limit(max_output_bytes + 1, max_output_bytes)
    return chunk


def _recover_corrupt_zlib(blob: bytes, max_output_bytes: int) -> bytes:
    """Salvage the longest decodable prefix of a mid-stream-corrupted zlib body.

    zlib discards *all* output produced by the decompress() call that raises,
    so recovery granularity at the failure point decides how much survives.
    A coarse chunked pass locates the failing region cheaply, then a replay
    pass advances byte-by-byte inside it.
    """
    step = 1 << 12
    d = zlib.decompressobj()
    fail_at = 0
    produced = 0
    while fail_at < len(blob):
        try:
            chunk = _decompress_zlib_chunk(
                d,
                blob[fail_at : fail_at + step],
                produced,
                max_output_bytes,
            )
        except zlib.error:
            break
        produced += len(chunk)
        fail_at += step

    d = zlib.decompressobj()
    out = bytearray()
    try:
        out += _decompress_zlib_chunk(d, blob[:fail_at], len(out), max_output_bytes)
        for i in range(fail_at, min(fail_at + step, len(blob))):
            out += _decompress_zlib_chunk(d, blob[i : i + 1], len(out), max_output_bytes)
    except zlib.error:
        pass
    return bytes(out)
