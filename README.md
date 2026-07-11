# CursorTrack

[![CI Status](https://github.com/mynkpdr/cursortrack/actions/workflows/ci.yml/badge.svg)](https://github.com/mynkpdr/cursortrack/actions)
[![License](https://img.shields.io/github/license/mynkpdr/cursortrack)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](pyproject.toml)

**CursorTrack** is a professional, open-source, developer-friendly mouse and input tracking tool and Python library. It records mouse movements, clicks, and scrolls into a compact, delta-encoded, crash-safe binary format, and can play them back or export them to CSV, JSONL, NumPy (`.npy`), and Parquet formats for machine learning pipelines.

v0.2 provides a first-class, dependency-free experience on **Windows** (native Win32 APIs via `ctypes`) and **Linux** (X11/XTest via `ctypes`, including XWayland sessions), with an OS-abstracted backend architecture designed for a seamless macOS addition.

---

## Features

- **🟢 High-Fidelity Recording**: Sample cursor movements (up to 240+ Hz), mouse buttons (press/release), and discrete vertical/horizontal scroll events.
- **🖐️ Windows Precision Touchpad Experiment**: Reconstruct two-finger scrolling from standardized Raw Input/HID reports when modern apps such as Chrome and VS Code bypass legacy wheel hooks.
- **⚡ Dependency-Free Playback**: Emulate mouse coordinates and clicks natively on Windows (Win32) and Linux (X11/XTest) via ctypes — zero packages required to replay or capture position.
- **🔒 Playback Fail-Safe**: Instantly abort an active replay by moving your mouse manually into **any corner** of the screen or pressing the **Esc** key globally.
- **📦 Crash-Safe Stream**: Buffers flush and `fsync` periodically to disk so that recordings are fully readable even if the script is abruptly killed.
- **📊 Scientific Exporters**: Output tracks directly to CSV, JSON Lines, NumPy `.npy` arrays, or Parquet for analysis, training, and simulation in ML pipelines.
- **🐍 Clean Programmatic API**: Use `Session` objects inside Jupyter notebooks and convert recordings to `pandas.DataFrame` tables with a single method call.

---

## Installation

Install CursorTrack using `pip` from the local directory:

```bash
# Core installation (move-only capture, no heavy deps)
pip install .

# Install with click/scroll capture support (requires pynput)
pip install .[windows]   # on Windows
pip install .[linux]     # on Linux

# Install with zstd compression support
pip install .[zstd]

# Install with ML libraries (numpy, pandas, pyarrow)
pip install .[ml]

# Full setup (including dev packages)
pip install .[dev,zstd,ml]
```

> [!NOTE]
> `pip install .` alone gives you movement-only recording and full playback/export — both use dependency-free `ctypes` calls (Win32 on Windows, X11/XTest on Linux). Recording **clicks or scrolls** additionally requires `pynput`, installed via the `[windows]` or `[linux]` extra.
>
> On Linux, CursorTrack talks to the X server, so it needs the standard X11 client libraries (`libX11` and `libXtst`, preinstalled on virtually every desktop distribution) and a running X11 or XWayland session (`DISPLAY` set). On headless machines, wrap commands with `xvfb-run`.

---

## Quickstart CLI

CursorTrack provides an ergonomic CLI built on **Typer** and styled with **Rich**:

### 1. Record a Session
```bash
# Record cursor moves and button clicks at 144Hz (default) to a timestamped file
cursortrack record --capture move,click

# Record all input gestures to session.ctrk for exactly 15 seconds
cursortrack record --capture all --seconds 15 -o session.ctrk
```

### 2. Replay a Recording
```bash
# Preview compatibility without injecting input
cursortrack play session.ctrk --dry-run

# Playback the session at double speed (strict layout/capability checks by default)
cursortrack play session.ctrk --speed 2.0

# Explicitly scale a recording onto a different desktop size
cursortrack play session.ctrk --mapping scale-to-bounds

# Correct scroll direction and replay at half scroll intensity
cursortrack play session.ctrk --invert-scroll --scroll-scale 0.5
```
> [!IMPORTANT]
> **FAIL-SAFE:** If a playback gets out of control, push the mouse cursor physically to any corner of your monitor or press the **Esc** key globally on your keyboard to stop emulation immediately.
>
> Playback never silently remaps coordinates. Use `--dry-run` to inspect source/target layout and capability mismatches, then choose an explicit `--mapping` (`absolute`, `scale-to-bounds`, `offset`, or `target-monitor`) when desktops differ. See [docs/playback-mapping.md](docs/playback-mapping.md).
>
> `--invert-scroll` and `--scroll-scale` are playback-only transformations:
> they do not modify the recording. Fractional scales retain remainder between
> events, so reducing intensity does not systematically discard small steps.

### 3. Display Session Info
```bash
cursortrack info session.ctrk
```

### 4. Export for ML Pipelines
```bash
# Export to standard CSV
cursortrack export session.ctrk --to csv

# Export to a NumPy array for modeling
cursortrack export session.ctrk --to npy
```

### 5. Check Environment Health
```bash
cursortrack doctor

# Windows only: verify raw two-finger scroll capture over Chrome or VS Code
cursortrack doctor --touchpad-test 15
```

On Windows, a recording that includes scroll capture uses Raw Input alongside
the normal mouse hook when a descriptor-compatible Microsoft Precision
Touchpad is present. Programmatic backend users must explicitly call
`request_enhanced_scroll_capture()` (or set
`CURSORTRACK_WINDOWS_TOUCHPAD=1`) because Windows permits only one Raw Input
target per device class in a process. CursorTrack refuses to replace another
in-process owner and falls back to the ordinary hook with a warning.

Set `CURSORTRACK_WINDOWS_TOUCHPAD=0` to force the original hook-only path.
Invalid environment values are rejected. If reconstructed scrolling is too
slow or too fast, set `CURSORTRACK_TOUCHPAD_STEP_FRACTION` between `0.002` and
`0.1` (default `0.012`; lower values produce more wheel steps). Runtime loss of
an active raw listener stops the recording and marks its recoverable prefix as
truncated rather than silently producing a session with missing scrolls.
`doctor --touchpad-test` exits nonzero when the descriptor is unsupported, the
listener fails, or no reconstructed event is observed. Invoking that diagnostic
explicitly tests Raw Input even when normal recording is disabled through
`CURSORTRACK_WINDOWS_TOUCHPAD=0`.

---

## Programmatic Library API

CursorTrack is fully accessible as a Python library:

```python
import pandas as pd
from cursortrack import Session

# Load a session recording
session = Session.load("session.ctrk")

print(f"Sample Rate: {session.rate} Hz")
print(f"Recorded events: {len(session.events)}")

# Convert directly to a Pandas DataFrame
df = session.to_dataframe()
print(df.head())
#   t          frame   type    x    y    button   sdx   sdy   touch_id
# 0 171994...  0       move    100  200  None     None  None  None
# 1 171994...  1       move    102  201  None     None  None  None
# 2 171994...  3       down    102  201  left     None  None  None
```

---

## Known Limitations (v0.2.x)

- **Native Wayland windows are out of reach on Linux.** The Linux backend connects through X11, which also covers XWayland windows on Wayland desktops. However, events delivered to *native* Wayland clients cannot be globally captured, and emulation targeting them is blocked by the compositor's sandboxing — this applies to every unprivileged tool, not just CursorTrack. Pure X11 sessions have no such restriction. See [docs/architecture.md](docs/architecture.md#5-linux-x11wayland-notes) for details.
- **Raw touch contacts and general multi-finger gestures are not stored.** The v2 format retains its reserved `TapEvent`/touch bit for compatibility, but current backends never synthesize touch events from mouse clicks and the CLI rejects `--capture touch`. On Windows, standardized Precision Touchpads have an experimental Raw Input path that reconstructs two-finger translation as discrete wheel steps. Vendor-specific/legacy touchpads, pinch zoom, three/four-finger gestures, and native inertial detail remain unsupported. Physical wheels continue through the ordinary hook, although a coincident same-direction step can be indistinguishable from a synthesized duplicate inside the short arbitration window. See [docs/architecture.md](docs/architecture.md#4-touch-and-gesture-boundary).

---

## Architecture and File Format

- Read [docs/architecture.md](docs/architecture.md) to learn how CursorTrack manages OS-independent layers.
- Check [docs/file-format.md](docs/file-format.md) for details on the compact varint + zigzag binary file structure.

---

## License

This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.
