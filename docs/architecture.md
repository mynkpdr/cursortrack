# Architecture and Design

This document details the modular system architecture of CursorTrack and its boundaries.

---

## 1. System Block Diagram

```mermaid
graph TD
    A[CLI / Programmatic User] --> B[cursortrack.core.session]
    B --> C[cursortrack.core.format]
    B --> D[cursortrack.core.codec]
    B --> E[cursortrack.core.events]
    
    A --> F[cursortrack.backends]
    F --> G[InputBackend Abstract Interface]
    G --> H[WindowsBackend Win32 ctypes/pynput]
    G --> I[LinuxBackend X11/XTest ctypes/pynput]
    G --> J[macOSBackend Stub]

    B --> K[cursortrack.export]
    K --> L[CSV Exporter]
    K --> M[JSONL Exporter]
    K --> N[NumPy Npy Exporter]
    K --> O[Parquet Exporter]
```

---

## 2. Package Boundaries

### `cursortrack/cli/`
The command-line parsing layer. It uses **Typer** and **Rich** to provide user interaction, terminal formatting, status updates, and progress bars. The CLI files are thin wrappers calling underlying library logic in `core/` and `export/`.

### `cursortrack/core/`
The programmatic core library.
- [format.py](../cursortrack/core/format.py) handles packing and unpacking file headers.
- [codec.py](../cursortrack/core/codec.py) manages raw integer encodings (varint/zigzag) and streaming compression writers.
- [events.py](../cursortrack/core/events.py) defines the structured dataclass hierarchy for input events (`MoveEvent`, `ButtonEvent`, etc.) and handles tag serialization.
- [layout.py](../cursortrack/core/layout.py) defines immutable coordinate-unit,
  monitor-layout, scale, and input-capability facts for the accepted v3
  portability contract. It does not enable a v3 reader/writer by itself.
- [playback/](../cursortrack/core/playback/) implements pure compatibility
  assessment and explicit coordinate mapping for portable replay (#50).
- [session.py](../cursortrack/core/session.py) exposes the primary developer API `Session` for programmatically loading, editing, saving, and analyzing tracks (e.g. converting to Pandas DataFrames).

### `cursortrack/backends/`
Encapsulates OS-specific interaction. Subclasses of `InputBackend` implement coordinates retrieval, mouse warping, and hardware click/scroll hooks. Calling code handles these actions through the abstraction, making platform support entirely additive.

Windows Precision Touchpad support is divided by responsibility:
- `_touchpad_scroll.py` contains platform-neutral frame assembly, translation,
  bounded wheel-step reconstruction, and duplicate arbitration.
- `_windows_hid.py` contains Win32 ABI declarations, Raw Input transport, and
  HID descriptor/report parsing.
- `_windows_touchpad.py` owns device state, the hidden-window message loop,
  registration lifecycle, probing, and configuration.

### `cursortrack/export/`
Translates parsed `Session` events into analytical standard formats. It handles CSV, JSON Lines, NumPy binary files, and optionally Parquet tables.

---

## 3. Playback Fail-Safe Architecture

To prevent simulated replays from capturing display focus and locking out human control, CursorTrack intercepts physical movement:
- During playback, before setting each virtual cursor position, the script queries the physical hardware cursor position using `backend.read_position()`.
- If the current cursor coordinate deviates from the expected coordinates and sits within 5 pixels of any monitor screen corner, a fail-safe trigger aborts execution immediately.

---

## 4. Touch and Gesture Boundary

Current backends expose cursor movement, mouse buttons, and wheel-style scroll
events only. They do not emit raw touch contacts, pressure, finger IDs, or
multi-finger gesture phases. Consequently:

- `cursortrack record --capture touch` is rejected rather than misrepresenting
  ordinary mouse clicks as `TapEvent` records.
- `--capture all` means all currently supported mouse events: move, click, and
  scroll.
- The v2 `CAP_TOUCH` bit and `TapEvent` tag remain decodable for file-format
  compatibility and possible future native backends. Playback of an existing
  tap retains its legacy left-click interpretation.

Windows has an additional experimental path for devices exposing Microsoft's
standard Precision Touchpad HID collection (usage page `0x0D`, usage `0x05`).
A hidden message-only window registers for `WM_INPUT` with `RIDEV_INPUTSINK`,
uses Contact Count and Scan Time to assemble parallel or hybrid packets, and
parses contact IDs, tip/confidence switches, and normalized X/Y values through
`HidP_*`. Confident parallel two-finger translation is then reconstructed as
discrete wheel steps. A short one-for-one arbitration window removes matching
duplicate steps when Windows also synthesizes `WM_MOUSEWHEEL`. The ordinary
`pynput` hook stays active for physical wheels and non-PTP devices. Arbitration
is necessarily best-effort: an unrelated same-direction wheel step arriving in
the same short window is not always distinguishable from a synthesized
duplicate.

Raw Input allows only one target window per device class in a process.
Therefore the standalone `record` CLI opts in only for scroll capture and
refuses to replace an existing in-process owner. Programmatic backend users
remain hook-only unless they call `request_enhanced_scroll_capture()` or set
`CURSORTRACK_WINDOWS_TOUCHPAD=1`. Registration, hidden-window creation, hook
startup, and teardown are treated as one owned lifecycle. A runtime parser or
listener failure is surfaced through `InputBackend.check_listener_health()` so
the recorder finalizes a truncated prefix instead of silently losing events.

Each device handle has independent frame and gesture state. Windows may omit
the device handle for Precision Touchpad packets; this is supported when one
compatible touchpad is active and rejected with a diagnostic when multiple
devices make attribution ambiguous.

This reconstruction cannot retain native pixel-level deltas, acceleration, or
inertia because the v2 event model stores integer wheel steps. It deliberately
rejects opposing two-finger motion (pinch/spread) and does not attempt
three/four-finger gestures. Vendor-specific legacy touchpads may not expose the
standard HID collection and therefore remain on the ordinary hook path. macOS
may report smooth or inertial deltas that cannot be represented faithfully as
integer wheel steps; native Wayland deliberately restricts global observation.

Persisting real touch contacts would still require a richer event model plus
separate platform implementations: the Windows Raw Input path currently uses
contacts only as an internal scroll signal, while macOS needs digitizer/event
tap semantics and Wayland needs compositor-approved APIs. Touch must not be
approximated from mouse click callbacks.

---

## 5. Linux (X11/Wayland) Notes

`LinuxBackend` mirrors the Windows backend's dependency-free design: it drives the X server directly through `ctypes` against `libX11`/`libXtst` (no Python packages needed for playback or position sampling), and reuses `pynput` for global click/scroll capture hooks.

**How each operation maps to X11:**
- `read_position()` → `XQueryPointer` on the root window.
- `set_position(x, y)` → `XWarpPointer` to root-window coordinates.
- `get_screen_size()` → `XDisplayWidth`/`XDisplayHeight` of the default screen.
- `click(button, pressed)` → `XTestFakeButtonEvent` (X buttons 1/2/3 for left/middle/right, 8/9 for x1/x2).
- `scroll(sdx, sdy)` → the X11 core protocol has no scroll-delta events; each wheel step is a press+release of buttons 4-7 (up/down/left/right).

**Why every injection is followed by `XSync`, not `XFlush`.** Xlib buffers protocol requests per connection. Flushing the buffer alone was observed (under Xvfb, with a `pynput` hook listening on a second connection) to leave `XTestFakeButtonEvent` requests undelivered to other clients' event hooks, while a full server round-trip (`XSync`) delivers them reliably. `pynput`'s own Linux controller syncs after every injection for the same reason. The cost is one round-trip per injected event, which is negligible against the recorder's sampling intervals.

**Threading.** `XInitThreads` is called before any other Xlib call so a single backend's display connection is safe to touch from both the recorder's sampling loop and the playback fail-safe polling.

**Wayland scope.** On Wayland desktops, CursorTrack connects to the XWayland compatibility server. Position reads, warps, and injected clicks work within the XWayland coordinate space, and capture hooks see events routed to X11 clients. What is *not* possible — for any unprivileged process, by compositor design — is globally capturing input delivered to native Wayland clients or injecting input into them. First-class native Wayland support would require the `org.freedesktop.portal.RemoteDesktop` portal (interactive permission prompts) or raw `/dev/input` access (root/`input` group); both are tracked in [ROADMAP.md](../ROADMAP.md).
