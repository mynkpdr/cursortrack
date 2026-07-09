"""Varint, zigzag, and streaming codec compression abstraction."""

from __future__ import annotations

import contextlib
import io
import os
import zlib
from typing import BinaryIO

# Codec constants
CODEC_RAW = 0
CODEC_ZSTD = 1
CODEC_ZLIB = 2

CODEC_NAME: dict[int, str] = {
    CODEC_RAW: "raw",
    CODEC_ZSTD: "zstd",
    CODEC_ZLIB: "zlib",
}


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
    shift = 0
    result = 0
    n = len(buf)
    while True:
        if pos >= n:
            return 0, pos, False
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos, True
        shift += 7


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
            if self._w is not None and self._zstd is not None:
                self._w.flush(self._zstd.FLUSH_FRAME)
                self._w.close()  # type: ignore[no-untyped-call]
        elif self.codec == CODEC_ZLIB and self._co is not None:
            self.f.write(self._co.flush(zlib.Z_FINISH))
        self.f.flush()
        with contextlib.suppress(OSError, ValueError):
            os.fsync(self.f.fileno())


def decompress_tolerant(blob: bytes, codec: int) -> bytes:
    """Decompress a (possibly unfinalized / truncated) body, keeping all valid decompressed bytes."""
    if codec == CODEC_RAW:
        return blob
    if codec == CODEC_ZSTD:
        try:
            import zstandard as zstd

            out = bytearray()
            reader = zstd.ZstdDecompressor().stream_reader(io.BytesIO(blob))
            try:
                while True:
                    chunk = reader.read(1 << 20)
                    if not chunk:
                        break
                    out += chunk
            except zstd.ZstdError:
                pass
            return bytes(out)
        except ImportError:
            # Fallback block in case zstd is missing on read (should be caught by validation,
            # but provide fallback for robustness).
            raise ImportError("zstd compression used but 'zstandard' is not installed.")
    if codec == CODEC_ZLIB:
        d = zlib.decompressobj()
        try:
            return d.decompress(blob)
        except zlib.error:
            # Return whatever was produced before the error (unfinalized streams)
            try:
                return d.decompress(blob, 0)
            except Exception:
                return b""
    raise ValueError(f"Unknown codec ID {codec}")
