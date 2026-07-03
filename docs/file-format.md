# Binary File Format Specification (v2)

CursorTrack uses a custom binary format (`.ctrk` or legacy `.curmov`) designed to minimize disk size for long, mostly idle mouse capture sessions. It utilizes **varint encoding**, **zigzag encoding**, **delta coordinates**, and **streaming compression**.

---

## 1. Header Layout

The binary file begins with a fixed-size header packed in little-endian format (no alignment padding):

| Field | Size | Type | Value / Description |
| :--- | :--- | :--- | :--- |
| **Magic** | 8 bytes | `char[8]` | `b"CURMOV02"` (Legacy v1 used `b"CURMOV01"`) |
| **Codec** | 1 byte | `uint8` | `0` = raw, `1` = zstd, `2` = zlib |
| **Sample Rate** | 2 bytes | `uint16` | Sampling frequency in Hz (samples per second) |
| **Screen Width** | 4 bytes | `int32` | Width of primary monitor screen in pixels (0 if unknown) |
| **Screen Height** | 4 bytes | `int32` | Height of primary monitor screen in pixels (0 if unknown) |
| **Start Time** | 8 bytes | `double` | Unix epoch time in seconds (matching frame 0) |
| **Initial X (x0)** | 4 bytes | `int32` | Absolute X pixel coordinate at start |
| **Initial Y (y0)** | 4 bytes | `int32` | Absolute Y pixel coordinate at start |
| **Capture Mask** | 1 byte | `uint8` | Bitmask: 1=move, 2=click, 4=scroll, 8=touch |

**Total Header Size**: 36 bytes.

---

## 2. Compressed Stream Body

Directly after the header follows the compressed event stream. If a codec is selected (e.g. `zstd` or `zlib`), the entire body is wrapped in compression frames. 

The decompressed stream is a sequence of **tagged events**. Each event starts with an unsigned varint (`uvarint`) **tag**, followed by a `uvarint` representing the **elapsed frames** since the previous event. This clock coordinate sharing ensures that events have accurate timing relative to the sampling rate, without needing individual absolute timestamps.

### Event Tags:

#### Tag 0: MOVE (Mouse Movement)
- **Tag**: `0`
- **Elapsed Ticks**: `dframes` (uvarint)
- **Delta X**: `dx` (signed varint, zigzag)
- **Delta Y**: `dy` (signed varint, zigzag)

#### Tag 1: DOWN (Button Press) / Tag 2: UP (Button Release)
- **Tag**: `1` (Down) or `2` (Up)
- **Elapsed Ticks**: `dframes` (uvarint)
- **Button ID**: `button` (uvarint, 0=left, 1=right, 2=middle, 3=x1, 4=x2)
- **Delta X**: `dx` (signed varint, zigzag)
- **Delta Y**: `dy` (signed varint, zigzag)

#### Tag 3: SCROLL (Mouse Wheel Rotation)
- **Tag**: `3`
- **Elapsed Ticks**: `dframes` (uvarint)
- **Scroll Delta X**: `sdx` (signed varint, zigzag)
- **Scroll Delta Y**: `sdy` (signed varint, zigzag)
- **Delta X**: `dx` (signed varint, zigzag)
- **Delta Y**: `dy` (signed varint, zigzag)

#### Tag 4: TAP (Touchpad Gesture Tap)
- **Tag**: `4`
- **Elapsed Ticks**: `dframes` (uvarint)
- **Touch Pointer ID**: `touch_id` (uvarint)
- **Delta X**: `dx` (signed varint, zigzag)
- **Delta Y**: `dy` (signed varint, zigzag)

---

## 3. Encodings Explained

### Varint (Variable-length Integer)
We encode integers in a variable-length stream of 7-bit blocks. The most significant bit (MSB) of each byte acts as a continuation flag:
- If MSB = `1`, another byte follows.
- If MSB = `0`, this is the final byte.

This maps small unsigned values to a single byte:
- Value `0` .. `127` -> `1 byte`
- Value `128` .. `16383` -> `2 bytes`

### Zigzag Encoding
Signed integers (deltas like `dx` and `dy`) can be negative. Standard two's complement numbers have `1`s in their most significant bits (e.g. `-1` is `0xFFFFFFFF`), which would cost 5 or 10 bytes in a naive varint encoder.
Zigzag mapping alternates positive and negative values so that small absolute values produce small unsigned integers:
- `0` -> `0`
- `-1` -> `1`
- `1` -> `2`
- `-2` -> `3`
- `2` -> `4`

Formula: `zigzag(n) = (abs(n) << 1) - (1 if n < 0 else 0)`.
This guarantees that small movement jumps (e.g. `dx = -1, dy = 2`) only take 1 byte each.

---

## 4. Legacy v1 Format

The original prototype format (`MAGIC_V1 = b"CURMOV01"`) is a stripped-down, move-only predecessor to v2:

| Field | Size | Type | Value / Description |
| :--- | :--- | :--- | :--- |
| **Magic** | 8 bytes | `char[8]` | `b"CURMOV01"` |
| **Codec** | 1 byte | `uint8` | `0` = raw, `1` = zstd, `2` = zlib |
| **Sample Rate** | 2 bytes | `uint16` | Sampling frequency in Hz |
| **Screen Width** | 4 bytes | `int32` | Width of primary monitor screen in pixels |
| **Screen Height** | 4 bytes | `int32` | Height of primary monitor screen in pixels |
| **Start Time** | 8 bytes | `double` | Unix epoch time in seconds (matching frame 0) |
| **Initial X (x0)** | 4 bytes | `int32` | Absolute X pixel coordinate at start |
| **Initial Y (y0)** | 4 bytes | `int32` | Absolute Y pixel coordinate at start |

**Total Header Size**: 35 bytes (no capture mask field — v1 files always captured movement only).

The v1 body has no tags: each event is simply a pair of zigzag-encoded `uvarint` deltas (`dx`, `dy`), one movement sample per event, with no `dframes` field (each event advances the frame counter by exactly 1). `Session.load()` and `read_header()` detect the magic bytes automatically and decode v1 files transparently — there is no need to convert them.

---

## 5. Versioning Policy

The 8-byte magic string (`CURMOV0<N>`) is the format's compatibility contract. It exists so a reader can always tell, from the first 8 bytes alone, exactly how to parse the rest of the file — including files written years apart by different versions of CursorTrack.

**Requires a new magic / major version bump** (e.g. `CURMOV03`):
- Changing the header layout (adding, removing, reordering, resizing, or retyping any fixed field).
- Changing the meaning of an existing event tag's fields, or changing how `dframes`/deltas are computed.
- Removing support for decoding an older magic version.

**Does NOT require a version bump** (safe, backward-compatible additions):
- Adding a **new event tag** with a value not previously used (readers must already treat unknown tags as a hard stop — see `iter_events_v2`, which is intentionally conservative rather than silently skipping unknown bytes).
- Adding a new codec ID to `CODEC_NAME`, as long as `CODEC_RAW`/`CODEC_ZSTD`/`CODEC_ZLIB` values are unchanged.
- New CLI flags, export formats, or library APIs that don't touch the on-disk byte layout.

**Compatibility guarantee**: every version of `cursortrack` commits to being able to *read* every magic version that has ever shipped (v1 included). Writing is always done in the latest format. There is no plan to drop v1 read support — the parsing cost of an extra branch in `read_header()` is negligible against the cost of silently orphaning old recordings.

---

## 6. Interchange Export Schemas (JSONL / NumPy)

Unlike the binary `.ctrk` format, the JSONL and `.npy` exports are flat, ML-friendly interchange formats rather than a versioned wire protocol — there is no magic byte or version field. `Session.load()` re-parses them by file extension. To make that round trip lossless, both formats repeat the session's rate/screen/capture metadata **on every row**, since it's constant across the file and this keeps each row/line self-describing even if only part of the file is read.

### JSONL

Each line is one JSON object: `ev.to_dict()` (varies by event `type` — see the field tables in section 2) plus:

| Key | Description |
| :--- | :--- |
| `t` | Absolute Unix timestamp (`session.start_time + frame / rate`) |
| `rate` | Sample rate in Hz, repeated on every line |
| `scr_w`, `scr_h` | Screen resolution in pixels, repeated on every line |
| `capture` | Capture bitmask, repeated on every line |

`Session.load_jsonl()` reads `rate`/`scr_w`/`scr_h`/`capture` from the **first** line only (they're constant). Files written before this metadata existed simply omit these keys; the loader falls back to `rate=144, scr_w=0, scr_h=0, capture=15` in that case, matching its historical behavior.

### NumPy (`.npy`)

A single 2D `float64` array, one row per event, columns:

| Index | Column | Description |
| :--- | :--- | :--- |
| 0 | `t` | Absolute Unix timestamp |
| 1 | `x` | Absolute X coordinate |
| 2 | `y` | Absolute Y coordinate |
| 3 | `type_id` | `0`=move, `1`=down, `2`=up, `3`=scroll, `4`=tap |
| 4 | `aux1` | button ID (down/up), `sdx` (scroll), or `touch_id` (tap) |
| 5 | `aux2` | `sdy` (scroll only); `0.0` otherwise |
| 6 | `rate` | Sample rate in Hz, repeated on every row |
| 7 | `scr_w` | Screen width in pixels, repeated on every row |
| 8 | `scr_h` | Screen height in pixels, repeated on every row |
| 9 | `capture` | Capture bitmask, repeated on every row |

`Session.load_npy()` reads columns 6-9 from row 0 when present. Files with 6 or fewer columns (exported before this metadata was added) fall back to the same legacy defaults as JSONL (`rate=144, scr_w=0, scr_h=0, capture=15`). An empty session still exports as a `(0, 10)` array rather than a 1D `(0,)` array, so it reloads cleanly instead of failing the "must be 2D" check.

Both formats are read-compatible forever under these rules: adding new trailing columns/keys is safe (old readers ignore them via `.get()`/column-count checks); removing or reordering existing ones is not.
