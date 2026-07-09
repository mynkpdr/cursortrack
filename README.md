# CursorTrack

[![CI Status](https://github.com/mynkpdr/cursortrack/actions/workflows/ci.yml/badge.svg)](https://github.com/mynkpdr/cursortrack/actions)
[![License](https://img.shields.io/github/license/mynkpdr/cursortrack)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](pyproject.toml)

**CursorTrack** is a professional, open-source, developer-friendly mouse and input tracking tool and Python library. It records mouse movements, clicks, scrolls, and touchpad gestures into a compact, delta-encoded, crash-safe binary format, and can play them back or export them to CSV, JSONL, NumPy (`.npy`), and Parquet formats for machine learning pipelines.

CursorTrack provides a first-class, dependency-free experience on **Windows** (native Win32 APIs via `ctypes`), **Linux** (X11/XTest via `ctypes`, including XWayland sessions), and **macOS** (CoreGraphics via `ctypes`, requires Accessibility permission — see [Known Limitations](#known-limitations-v030)), with a shared OS-abstracted backend architecture.

---

## Features

- **🟢 High-Fidelity Recording**: Sample cursor movements (up to 240+ Hz), mouse buttons (press/release), scrolls (vertical + horizontal), and touchpad gestures.
- **⚡ Dependency-Free Playback**: Emulate mouse coordinates and clicks natively on Windows (Win32), Linux (X11/XTest), and macOS (CoreGraphics) via ctypes — zero packages required to replay or capture position.
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

# Install with click/scroll/touch capture support (requires pynput)
pip install .[windows]   # on Windows
pip install .[linux]     # on Linux
pip install .[macos]     # on macOS

# Install with zstd compression support
pip install .[zstd]

# Install with ML libraries (numpy, pandas, pyarrow)
pip install .[ml]

# Full setup (including dev packages)
pip install .[dev,zstd,ml]
```

> [!NOTE]
> `pip install .` alone gives you movement-only recording and full playback/export — all three platforms use dependency-free `ctypes` calls (Win32 on Windows, X11/XTest on Linux, CoreGraphics on macOS). Recording **clicks, scrolls, or touch gestures** additionally requires `pynput`, installed via the `[windows]`, `[linux]`, or `[macos]` extra.
>
> On Linux, CursorTrack talks to the X server, so it needs the standard X11 client libraries (`libX11` and `libXtst`, preinstalled on virtually every desktop distribution) and a running X11 or XWayland session (`DISPLAY` set). On headless machines, wrap commands with `xvfb-run`.
>
> On macOS, CursorTrack requires **Accessibility permission** (System Settings → Privacy & Security → Accessibility) for both cursor emulation (`play`) and click/scroll capture (`record --capture click,scroll,touch`). Without it, CoreGraphics silently drops injected events and `pynput`'s hooks never fire — no error is raised. Run `cursortrack doctor` to check whether it's granted.

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
# Playback the session at double speed
cursortrack play session.ctrk --speed 2.0
```
> [!IMPORTANT]
> **FAIL-SAFE:** If a playback gets out of control, push the mouse cursor physically to any corner of your monitor or press the **Esc** key globally on your keyboard to stop emulation immediately.

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
```

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

## Known Limitations (v0.3.0)

- **macOS requires Accessibility permission for both emulation and capture.** Grant it under System Settings → Privacy & Security → Accessibility, or `set_position`/`click`/`scroll` silently do nothing and `record --capture click,scroll,touch` silently captures nothing — CoreGraphics and `pynput` raise no error either way. `read_position`/`get_screen_size` work without it. Run `cursortrack doctor` to check. See [docs/architecture.md](docs/architecture.md#6-macos-coregraphics-notes) for details.
- **macOS side-button (x1/x2) clicks cannot be captured with their own identity.** `pynput`'s macOS listener reports every non-left/right button press as `"middle"`, since it does not disambiguate `kCGEventOtherMouseDown/Up` by button number. This only affects *recording*; `play` still emulates x1/x2 correctly since that path talks to CoreGraphics directly. See [docs/architecture.md](docs/architecture.md#6-macos-coregraphics-notes).
- **`get_screen_size()` reports only the main display** on Windows and macOS (Linux's `XDisplayWidth`/`Height` has the same single-screen scope for the default screen). Multi-monitor-aware fail-safe/bounds behavior is tracked as future work.
- **Native Wayland windows are out of reach on Linux.** The Linux backend connects through X11, which also covers XWayland windows on Wayland desktops. However, events delivered to *native* Wayland clients cannot be globally captured, and emulation targeting them is blocked by the compositor's sandboxing — this applies to every unprivileged tool, not just CursorTrack. Pure X11 sessions have no such restriction. See [docs/architecture.md](docs/architecture.md#5-linux-x11wayland-notes) for details.
- **Multi-finger touchpad gestures** (pinch-to-zoom, rotate, 3-finger app-switch, 4-finger virtual-desktop-switch) cannot be captured. Windows reserves these for its own shell-level gesture handling and never exposes them to background apps through any API — this isn't something CursorTrack (or any equivalent tool) can work around.
- **Two-finger scroll may not be captured on some touchpads**, even with `--capture scroll` or `all` and `pynput` installed. Physical/USB mouse wheel scrolling is unaffected and always captured. See [docs/architecture.md](docs/architecture.md#4-touchpad-gesture-capture-limitations) for why.

---

## Architecture and File Format

- Read [docs/architecture.md](docs/architecture.md) to learn how CursorTrack manages OS-independent layers.
- Check [docs/file-format.md](docs/file-format.md) for details on the compact varint + zigzag binary file structure.

---

## License

This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.
