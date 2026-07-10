# RFC 0001: Portable Session Format v3

- Status: Draft
- Target milestone: v0.3.0
- Tracking issue: #51
- Compatibility: v1/v2 readers remain supported; v3 uses a new magic value

## Summary

CursorTrack v1/v2 files are portable as data but not as reliable automation.
They store absolute coordinates and only a width/height pair, omitting the
virtual origin, monitor topology, coordinate unit, display scale, source
backend, and scroll semantics needed to assess another target.

Format v3 adds:

1. Extensible source-layout and capability metadata.
2. Monotonic microsecond event timing.
3. Length-framed events with a common state prefix.
4. Independently compressed and checksummed chunks.
5. A clean-close footer that distinguishes complete from interrupted files.

The format describes what was recorded. It never silently decides how one
desktop maps onto another; mapping remains an explicit playback policy in #50.

## Normative language

The terms MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are normative. All
fixed-width integers are unsigned little-endian unless a field explicitly says
otherwise.

## Goals

- Detect incompatible source and target layouts before input injection.
- Represent negative origins and non-rectangular multi-monitor layouts.
- Distinguish physical pixels, logical points, unknown units, and backend units.
- Preserve discrete wheel semantics without claiming cross-platform precision.
- Keep v1/v2 readable indefinitely.
- Skip unknown event tags without losing time or pointer state.
- Recover every validated chunk prefix after interruption.
- Keep all allocation, decompression, iteration, and playback work bounded.

## Non-goals

- Inferring semantic equivalence between unrelated application interfaces.
- Bypassing native Wayland isolation, Windows secure desktop/UIPI, or macOS TCC.
- Storing screenshots, window titles, hostnames, usernames, or hardware serials.
- Encryption, authentication, or tamper-proof audit records.
- Raw touch/gesture capture before a real backend and event model exist.
- Recovery after a corrupt middle chunk in v3.0.

## Compatibility

The first eight bytes select the parser:

| Version | Magic |
| --- | --- |
| v1 | `CURMOV01` |
| v2 | `CURMOV02` |
| v3 | `CURMOV03` |

A v3 writer MUST NOT emit v1/v2 bytes under the v3 magic. `Session.load()`
continues dispatching v1/v2 to their existing readers. Older CursorTrack
versions reject v3 because they do not recognize its magic.

Conversion is never in place. A future converter may rewrite v1/v2 as v3, but
facts absent from the source MUST remain unknown rather than inferred.

## File structure

```text
+------------------------------+
| 20-byte fixed prelude        |
+------------------------------+
| UTF-8 metadata JSON          |
+------------------------------+
| 24-byte chunk header 0       |
| compressed payload 0         |
+------------------------------+
| ...                          |
+------------------------------+
| 32-byte clean-close footer   |
+------------------------------+
```

A correctly prefixed partial final chunk/footer or EOF at a chunk boundary is
recoverable truncation under the deterministic rules below. Bytes after a valid
footer are corruption.

## Fixed prelude

The prelude is exactly 20 bytes:

| Offset | Size | Field | Rule |
| ---: | ---: | --- | --- |
| 0 | 8 | magic | MUST be `CURMOV03` |
| 8 | 2 | flags | MUST be zero in v3.0 |
| 10 | 1 | codec | `0=raw`, `1=zstd`, `2=zlib` |
| 11 | 1 | reserved | MUST be zero |
| 12 | 4 | metadata_length | UTF-8 metadata byte count |
| 16 | 4 | metadata_crc32 | CRC of stored metadata bytes |

Readers MUST reject nonzero flags/reserved values, unknown codecs, or metadata
length beyond configured limits before allocating. The initial metadata limit
is 1 MiB.

If EOF occurs before 20 bytes, the file is truncated only when all available
magic bytes exactly match a prefix of `CURMOV03`; otherwise it has invalid
magic. EOF after a semantically valid prelude but before `metadata_length` bytes
is truncation. A complete metadata payload with invalid UTF-8, JSON, schema, or
CRC is corruption/invalid input, not truncation.

## CRC definition

Every CRC field uses CRC-32/ISO-HDLC, the result returned by
`zlib.crc32(data) & 0xffffffff` with initial value zero. The check value for
ASCII `123456789` is `0xcbf43926`.

CRCs detect accidental corruption. They are not cryptographic signatures and
do not relax any decoder limit.

## Codec definitions

The prelude codec applies to every chunk in the file:

- `0 raw`: payload is uncompressed and `compressed_length == raw_length`.
- `1 zstd`: payload is exactly one standard Zstandard frame (RFC 8878).
  Concatenated frames, skippable frames, and trailing bytes are rejected.
- `2 zlib`: payload is exactly one RFC 1950 zlib stream. Raw DEFLATE,
  concatenated streams, and trailing bytes are rejected.

Conforming readers MUST support raw and zlib. Zstandard MAY remain an optional
dependency; a reader without it fails clearly before event decoding. Readers
MUST apply a configured Zstandard window-memory limit in addition to raw output
limits.

The guaranteed interoperability profile is raw or zlib. CursorTrack's reference
writer uses zlib level 6 and zstd level 3, but compressed bytes are not required
to match across compressor implementations. Byte-exact golden files use the raw
codec; compressed fixtures assert decoded bytes, lengths, EOF, and CRC rather
than compressor output identity.

## Metadata JSON

Metadata is one RFC 8259 JSON object encoded as UTF-8. Duplicate object keys,
non-finite numbers, excessive nesting, and values beyond configured string,
array, member, or numeric-digit limits are rejected.

Conforming writers MUST serialize metadata with the JSON Canonicalization
Scheme (RFC 8785). Readers accept any bounded RFC 8259 representation satisfying
the schema. Canonical writing makes raw-codec golden fixtures reproducible; CRC
validation still covers the exact stored bytes.

Unknown keys are ignored at every object depth unless a field table says
otherwise. Required v3.0 fields and invariants are normative:

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | integer | MUST equal `3` |
| `started_at_unix_us` | integer | nonnegative informational Unix microseconds |
| `clock` | object | MUST contain `unit=microsecond` and `source=monotonic` |
| `source` | object | required `os` and `backend` strings, each at most 64 UTF-8 bytes |
| `desktop` | object | coordinate/layout schema below |
| `initial_pointer` | object | required signed `x`/`y` in the desktop unit |
| `capture_requested` | array | unique subset of `move`, `click`, `scroll`, `touch` |
| `input` | object | button and scroll semantics below |

All metadata integers MUST be exactly representable JSON/I-JSON integers in
`[-(2^53-1), 2^53-1]`; narrower field ranges still apply. `capture_requested`
is ordered by the canonical sequence `move`, `click`, `scroll`, `touch`, with
unrequested names omitted.

Readers ignore unknown `source` keys. Conforming writers MUST NOT add hostnames,
usernames, device names, or stable hardware identifiers. `os` is one of
`windows`, `linux`, `macos`, or `other`.

### Desktop schema

```json
{
  "known": true,
  "coordinate_unit": "backend-unit",
  "coordinate_unit_id": "x11-root-v1",
  "bounds": {"x": -1920, "y": 0, "width": 4480, "height": 1440},
  "monitors": [
    {
      "id": "source-0",
      "primary": false,
      "bounds": {"x": -1920, "y": 0, "width": 1920, "height": 1080},
      "scale": null,
      "rotation": 0
    },
    {
      "id": "source-1",
      "primary": true,
      "bounds": {"x": 0, "y": 0, "width": 2560, "height": 1440},
      "scale": {"numerator": 5, "denominator": 4},
      "rotation": 0
    }
  ]
}
```

Required desktop fields:

| Field | Type | Rule |
| --- | --- | --- |
| `known` | boolean | topology/bounds availability |
| `coordinate_unit` | string | enum defined below |
| `coordinate_unit_id` | string or null | nonempty, at most 64 UTF-8 bytes when required |
| `bounds` | rectangle object or null | required object when `known=true` |
| `monitors` | array or null | required nonempty array when `known=true` |

Every rectangle object has four required integer-valued fields:
`x`, `y`, `width`, and `height`; unknown fields are ignored. Every monitor has
required `id:string`, `primary:boolean`, `bounds:rectangle`,
`scale:object-or-null`, and `rotation:integer` fields. A scale object has
required positive uint32 `numerator` and `denominator` fields, MUST be reduced
to lowest terms, and has denominator nonzero.

Desktop rules:

- X increases rightward and Y downward.
- Rectangles are half-open: `[x, x+width) × [y, y+height)`.
- `x`/`y` are signed 32-bit integers; width/height are positive uint32 values.
- `coordinate_unit` is `physical-pixel`, `logical-point`, `backend-unit`, or
  `unknown`.
- `coordinate_unit_id` is required for `backend-unit`, otherwise null.
- Scale is physical pixels per logical point as a positive reduced rational, or
  null when unknown. Floats are not used.
- Rotation is one of `0`, `90`, `180`, or `270`.
- Monitor IDs are unique nonempty UTF-8 strings of at most 64 bytes and are
  file-local labels with no meaning on another machine.
- Writers order monitors by `(bounds.x, bounds.y, bounds.width, bounds.height,
  id_utf8)`, comparing the final ID as unsigned UTF-8 bytes lexicographically;
  readers do not attach semantic meaning to array order.
- When `known=true`, bounds and an exhaustive non-empty monitor list are
  required, exactly one monitor is primary, and bounds equal the monitor union's
  bounding rectangle. Overlap (cloning) and gaps are allowed.
- When `known=false`, bounds and monitors MUST be null. The coordinate unit MAY
  still be known; its ID follows the same `backend-unit` rule.

`initial_pointer.x/y` are signed 32-bit integers. If desktop topology is
unknown, they remain valid in the declared coordinate unit but cannot support
generic layout mapping.

Backends MUST use `unknown` unless they can establish their unit. X11/XWayland
coordinates are not automatically physical pixels. Win32 coordinates are
physical only when effective DPI awareness is verified. CoreGraphics unit
selection remains unknown until physical testing establishes it.

### Input schema

```json
{
  "buttons": ["left", "right", "middle", "x1", "x2"],
  "scroll_unit": "wheel-detent",
  "precise_scroll": false
}
```

The input object requires `buttons:array`, `scroll_unit:string`, and
`precise_scroll:boolean`; unknown keys are ignored.

`buttons` is a unique array in canonical order, containing only
`left`, `right`, `middle`, `x1`, and `x2`. Every DOWN/UP button ID in the stream
MUST name an entry in this array; IDs above 4 are invalid in v3.0. New buttons
require a future format extension.

v3.0 supports `scroll_unit=wheel-detent` only and requires
`precise_scroll=false`. A driver delta is not called a line because line
interpretation belongs to applications. Pixel/fixed-point precision requires a
future event tag and capability RFC.

`capture_requested` records configuration, not a claim that every event class
was observed. Actual contents are derived from decoded records.

## Chunk framing

Each chunk has a 24-byte header followed by one complete codec frame:

| Offset | Size | Field | Rule |
| ---: | ---: | --- | --- |
| 0 | 4 | marker | MUST be `CTCH` |
| 4 | 4 | sequence | starts at zero, increments by one |
| 8 | 4 | compressed_length | payload bytes |
| 12 | 4 | raw_length | exact decompressed bytes |
| 16 | 4 | event_count | on-disk records in payload |
| 20 | 4 | raw_crc32 | CRC of decompressed payload |

Each complete encoded record, including its `record_length` varint, MUST be at
most 64 KiB. Before appending a record, the
reference writer seals a nonempty current chunk when either:

- adding the record would make raw payload length exceed 65,536 bytes; or
- the record's elapsed time is at least 1,000,000 microseconds after the first
  record in the current chunk.

The threshold-crossing record starts the next chunk. A chunk that reaches
exactly 65,536 bytes is sealed immediately. Independently, the live recorder
seals a nonempty chunk when one monotonic wall-clock second has elapsed since
its first record, even if no new event arrives. No chunk is created during a
fully idle period. Empty chunks are forbidden.

Readers validate lengths before reading/decompressing, require exact codec EOF,
require exact `raw_length`, validate CRC, decode exactly `event_count` records,
and reject trailing raw bytes.

v3.0 recovery stops at the first partial or invalid chunk. Readers MUST NOT scan
for another `CTCH` marker. Because time and coordinate state chain across chunks,
post-corruption continuation is not safe without independently checksummed base
state; that is deferred.

## Event integer encoding

Unsigned fields use minimal unsigned LEB128, limited to 10 bytes and uint64.
Non-minimal encodings and overflow are rejected.

Signed fields use ZigZag then unsigned LEB128:

```text
zigzag(n) = (abs(n) << 1) - (1 if n < 0 else 0)
```

ZigZag inputs are signed 64-bit integers. Decoders use checked arithmetic:

- accumulated elapsed microseconds MUST remain within uint64 and the configured
  duration limit;
- accumulated X/Y MUST remain signed 32-bit after every `dx/dy`;
- scroll deltas and tag-specific IDs MUST remain within their configured and
  tag-defined ranges.

Overflow never wraps and is invalid input, even in languages whose native
integer arithmetic would wrap.

Examples:

| Value | Encoding |
| ---: | --- |
| uvarint 0 | `00` |
| uvarint 127 | `7f` |
| uvarint 128 | `80 01` |
| uvarint 300 | `ac 02` |
| svarint 0 | `00` |
| svarint -1 | `01` |
| svarint 1 | `02` |

## Event framing

Every event has common state fields:

```text
record_length : uvarint
tag           : uvarint
delta_us      : uvarint
dx            : svarint
dy            : svarint
payload       : remaining record bytes
```

`record_length` counts all bytes after its own encoding. It is bounded before a
record slice is allocated.

Readers always decode `delta_us`, `dx`, and `dy`, advance elapsed time and
pointer state, then:

- decode a known tag's payload exactly; or
- skip an unknown tag's remaining payload at the record boundary.

This preserves state after unknown records. A session containing unknown tags
is marked as having unsupported events and strict playback refuses it even
though analytics may continue.

The programmatic decoder emits `UnknownEvent(frame, x, y, tag, raw_payload)` so
unknown records remain visible in `Session.events`, including a final unknown
record. `Session.unsupported_event_count` counts them. Unknown records count
toward chunk/footer event totals and configured event limits.

v3.0 tags:

| Tag | Name | Tag-specific payload |
| ---: | --- | --- |
| 0 | MOVE | empty |
| 1 | DOWN | `button:uvarint` |
| 2 | UP | `button:uvarint` |
| 3 | SCROLL | `sdx:svarint`, `sdy:svarint` |
| 4 | TAP | `touch_id:uvarint` |

Button IDs 0-4 retain left/right/middle/x1/x2 meanings; larger IDs are invalid.
TAP preserves the event class when converting a v2 file, but conversion is not
described as lossless because v2 timing and layout facts may not map exactly.
Current v3 writers do not capture raw touch.

Time and pointer state begin at zero and `initial_pointer`. `delta_us=0` is
allowed for simultaneous/quantized events. Given recording start `start_ns` and
event timestamp `event_ns`, writers compute:

```text
elapsed_us = max(previous_elapsed_us, (event_ns - start_ns) // 1000)
delta_us = elapsed_us - previous_elapsed_us
```

Writers MUST NOT subtract independently rounded/floored absolute microsecond
values.

The on-disk event count excludes the synthetic initial move. For API parity,
`Session.events` prepends `MoveEvent(frame=0, initial_pointer)`. v3 exposes
elapsed microseconds through the existing `frame` field and synthesizes
`Session.rate=1_000_000`; a future API may add clearer clock-named properties.

## Clean-close footer

A gracefully finalized file ends with exactly 32 bytes:

| Offset | Size | Field | Rule |
| ---: | ---: | --- | --- |
| 0 | 8 | marker | `CTEND03\0` |
| 8 | 4 | chunk_count | number of validated chunks |
| 12 | 8 | total_event_count | on-disk events, excluding synthetic initial move |
| 20 | 8 | total_duration_us | sum of all event deltas |
| 28 | 4 | footer_crc32 | CRC of preceding 28 footer bytes |

Counts and duration MUST equal decoded values. A valid empty session has zero
chunks/events/duration followed by a footer. A missing/partial footer marks the
validated chunk prefix truncated. An invalid footer is corruption.

At every chunk boundary, the next bytes MUST begin with `CTCH`, `CTEND03\0`, or
EOF. Classification is deterministic:

- EOF at a boundary means truncation because the footer is absent.
- A remaining byte sequence shorter than a complete marker is truncation only
  when it exactly matches a prefix of `CTCH` or `CTEND03\0`; otherwise corruption.
- A complete `CTCH` followed by a partial 24-byte header is truncation. After a
  complete header passes all marker, sequence, length, and limit validation, EOF
  before its declared payload length is truncation.
- A complete `CTEND03\0` followed by a partial footer is truncation.
- Any marker mismatch, semantically invalid complete header/footer (including
  over-limit lengths), or bytes after a valid footer is corruption.

## Integrity and safe playback

`Session.integrity` remains:

- `complete`: metadata, chunks, and clean-close footer validate.
- `truncated`: a validated prefix ends before a complete final chunk/footer.
- `corrupt-recovered`: a validated prefix precedes corruption.

Invalid metadata, unsupported codec, limit violation, sequence regression,
checksum mismatch, malformed known payload, or invalid footer raises
`ValueError` in strict validation. Tolerant loading may return only the
validated prefix with `corrupt-recovered`; it never resumes after corruption.

Playback defaults MUST refuse:

- non-complete integrity;
- unknown/skipped event tags;
- incompatible or unknown required target capabilities;
- unbalanced button state;
- layout mismatch without an explicit mapping;
- configured action-rate, duration, scroll-magnitude, or coordinate limits.

Button-state validation tracks a set of pressed canonical buttons. DOWN for an
already pressed button, UP for a button not pressed, or a nonempty pressed set
at session end marks button state invalid for playback. Analytical loading MAY
retain these events and expose `Session.button_state_valid=false`; strict
playback refuses them.

Playback retains fail-closed position checks and releases held buttons on every
exit path.

## Decoder limits

Limits are enforced before allocation or iteration:

- metadata bytes, depth, members, array items, strings, and numeric digits;
- compressed/raw bytes per chunk and total;
- Zstandard window memory;
- chunks, events, and record length;
- varint width and value;
- accumulated duration;
- coordinates, deltas, scroll values, and touch/button IDs;
- materialized event count.

`DecodeLimits` gains v3 fields while retaining v1/v2 behavior. Trusted callers
may explicitly opt into larger limits.

## Backend metadata API

Before enabling the writer, `InputBackend` needs typed APIs equivalent to:

```python
def get_layout(self) -> DesktopLayout:
    """Return known layout facts or an explicit unknown layout."""

def get_capabilities(self) -> InputCapabilities:
    """Return coordinate, button, scroll, capture, and injection semantics."""
```

Backends MUST report uncertainty instead of guessing. Tests cover pure metadata
models on all platforms; physical multi-monitor/DPI behavior remains a manual
release gate.

`InputCapabilities` minimally contains:

- `coordinate_unit` and optional backend-unit ID;
- known canonical button set;
- known scroll-unit set and precise-scroll boolean;
- booleans for position read, position injection, button injection, scroll
  injection, global button capture, and global scroll capture;
- an explicit restrictions list (for example `xwayland-only`,
  `interactive-desktop-only`, or `permission-required`).

This RFC defines the source facts and capability vocabulary. #50 defines the
target comparison and transform algorithm; a v3 reader/writer does not depend
on that later playback policy.

## Programmatic and export compatibility

- `Session.version` is `3`; `Session.codec` is the single prelude codec.
- `Session.start_time` is `started_at_unix_us / 1_000_000`.
- `Session.integrity` follows the rules above and `Session.truncated` is true for
  both non-complete integrity states.
- Existing event `frame` values contain elapsed microseconds and
  `Session.rate=1_000_000`. This is an API compatibility bridge, not a sampling
  frequency; v3 documentation and `info` MUST label it as a timebase.
- `Session.screen_width/height` derive from known desktop bounds, otherwise zero.
  Their unit is `desktop.coordinate_unit`, not necessarily pixels; existing
  property documentation must be corrected.
- `Session.capture_mask` derives from `capture_requested`; callers inspect events
  to know what was actually observed.
- `Session.events` includes the synthetic initial move, while footer/chunk counts
  do not.
- CSV, Parquet, and DataFrame exports are allowed as analytical, lossy views.
- Current NumPy and row-only JSONL round-trip schemas cannot represent the v3
  timebase/layout (and currently reject rates above 65,535). Exporting v3 to
  those formats MUST fail clearly until a versioned interchange proposal lands;
  they MUST NOT emit files that their loaders reject or falsely call lossless.
- A separate interchange-schema proposal is required before any export claims
  lossless v3 round trips.

## Privacy

Display geometry, scale, source OS, and timestamp can fingerprint a setup.
Writers collect only compatibility facts and MUST omit host/application/user
identifiers. Loading and validation never trigger playback.

## Implementation and acceptance plan

The byte-exact golden profile uses raw codec, RFC 8785 metadata, a supplied
`start_ns`, supplied record-capture nanosecond timestamps, supplied recorder
timer-callback nanosecond timestamps, the normative monitor/button order, and
the chunk-threshold algorithm above. Fixture observations are processed by
`(timestamp_ns, kind)`, with timer callbacks before records at equal timestamps.
A timer seals a nonempty chunk exactly when
`callback_ns - chunk_first_record_ns >= 1_000_000_000`. Including the start and
timer observations makes timing and idle-triggered seals deterministic.
Compressed fixtures are semantic rather than byte-identical because valid
compressor implementations may differ.

1. Accept this RFC and commit byte-exact empty/single-event/multi-chunk golden fixtures.
2. Commit malformed fixtures for every length, CRC, codec, JSON, event, footer,
   and limit failure.
3. Add layout/capability dataclasses and backend unknown-state reporting.
4. Implement a v3 reader with no writer enabled.
5. Implement chunk/event encoders and compare against golden bytes.
6. Add recorder layout discovery for Windows and X11; uncertain units remain unknown.
7. Add `info` and no-injection `validate` support.
8. Implement #50 strict preview and explicit transforms.
9. Enable v3 writing only after Windows/X11 CI and cross-implementation fixture
   tests pass.
10. Validate macOS metadata separately on physical Macs. This RFC does not
    modify the existing draft macOS PR.

## Resolved design decisions

- Chunks seal on both thresholds: one second or 64 KiB, whichever comes first.
- Writers use RFC 8785 canonical JSON; readers accept bounded RFC 8259 JSON.
- v3.0 supports wheel-detent scrolling only; precise scrolling is deferred.
- Recovery stops at the first invalid chunk; marker scanning is forbidden.
- Unknown platform coordinate facts use an explicit unknown representation.
