# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-09

This release brings first-class Linux support: recording, playback, and capture now work on X11 sessions (and XWayland on Wayland desktops) with the same dependency-free design as the Windows backend.

### Added
- **Linux Input Backend**: Implemented `LinuxBackend` using direct `ctypes` calls into `libX11`/`libXtst` — `XQueryPointer`/`XWarpPointer` for position sampling and warping, `XTestFakeButtonEvent` for click and scroll emulation (X11 wheel steps map to buttons 4-7). No Python packages are required for playback or movement recording; click/scroll capture reuses `pynput` via the `[linux]` extra.
- **Linux Integration Tests**: Real-backend test coverage (cursor warping, hook capture of XTest-injected events, full CLI record/play lifecycle) that runs under Xvfb in CI and skips gracefully without a display.
- **Full Linux CI Matrix**: The Ubuntu job now runs the complete test suite under `xvfb-run` across Python 3.9-3.14, matching the Windows matrix.
- **Linux-aware Diagnostics**: `cursortrack doctor` reports Linux as supported, detects whether an X11 display is available (suggesting `xvfb-run` for headless machines), and names the platform-appropriate install extra; `cursortrack devices` no longer labels the linux backend as a stub.

### Notes
- Injected X events are followed by `XSync` (a full server round-trip) rather than `XFlush`: flushing alone was observed to leave XTest fake events undelivered to other clients' capture hooks.
- On Wayland desktops CursorTrack operates through XWayland; input delivered to native Wayland clients cannot be captured or driven by unprivileged processes. Native Wayland support (portal APIs) is tracked in ROADMAP.md.

## [0.1.0] - 2026-07-03

This is the initial release of CursorTrack, providing modular cursor tracking, playback, and ML-oriented exports.

### Added
- **Modular Codebase**: Rewrote the single-file prototype into organized packages (`core`, `cli`, `backends`, `export`).
- **Modern Typer CLI**: Swapped raw `argparse` parsing for an type-hint-driven interface with styled help menus.
- **Rich Dashboard UI**: Added a live updating dashboard panel for recording progress and beautiful tables for file inspection and health checks.
- **Direct Win32 Emulation**: Implemented direct ctypes calls for Windows coordinate getting/setting and click/scroll simulation (bypassing pynput for playback).
- **Parquet Export Support**: Added support for exporting cursor recordings directly to Parquet tables (requires pandas + pyarrow).
- **Fail-Safe Handler**: Added quick mouse-to-corner abort detection to terminate playback easily if coordinates lose alignment.
- **Strict Development Quality**: Configured `ruff`, `mypy`, `pytest` test suites, and GitHub Actions CI.
- **Python 3.13 / 3.14 support**: verified and added to the tested version matrix alongside 3.9-3.12.

### Fixed
- CLI commands crashed on Python 3.9 with `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`. Typer resolves every command's type hints at runtime via `typing.get_type_hints()`, which forces evaluation of `X | None` annotations; that syntax requires `type.__or__` (PEP 604), added in Python 3.10. Switched the affected CLI parameters to `Optional[X]`, which works on every supported version.
- `cursortrack play` could crash at the very end of an otherwise-successful run if `pynput`'s keyboard listener failed to start or stop cleanly (observed as an `AttributeError` from a `pynput`/`python-xlib` version mismatch). The Esc-abort listener is now optional end-to-end: failures degrade to a warning instead of crashing playback.
