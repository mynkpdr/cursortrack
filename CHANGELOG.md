# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-07-09

A pure bug-fix release: twelve correctness and safety fixes across the codec, recorder, playback, CLI, and both OS backends. No file-format or breaking API changes; two behavior changes are called out below.

### Fixed
- **Codec: `write_uvarint` hung forever on negative input** (#4). It now raises `ValueError` immediately instead of appending continuation bytes unboundedly.
- **Codec: a corrupt byte in a zlib body lost the entire recording** (#5). Tolerant decompression now salvages the longest decodable prefix via chunked recovery with a byte-by-byte replay around the failure point.
- **Recorder: frame-clock drift** (#3). Click/scroll timestamps that rounded ahead of the sampling tick permanently stretched playback timing; frame bookkeeping now advances in lockstep with what is actually encoded.
- **Side/extra mouse buttons were recorded and replayed as left clicks** (#2). Linux capture normalizes pynput's `button8`/`button9` to `x1`/`x2`, the recorder drops unknown buttons with a one-time warning, and the Windows backend emits real `MOUSEEVENTF_XDOWN`/`XUP` events. Unknown button names in `click()` are now a no-op on both backends.
- **Linux: a dying X server terminated the whole process** (#6). Custom Xlib protocol/IO error handlers turn a lost connection into a catchable `RuntimeError`, and XTest availability is probed at startup with a clear error.
- **`play --quiet` skipped the `--delay` safety countdown** (#11). The delay is always honored; only the countdown messages are silenced. Ctrl-C during the countdown now exits cleanly (code 130) without leaking the Esc listener.
- **`export`/`record` silently overwrote existing files — including the export's own input** (#12). Both commands now refuse to overwrite unless `--force` is given, and exporting a file onto itself is always rejected.
- **Aborted playback exited 0 like success** (#13). Fail-safe/Esc aborts exit 1; Ctrl-C exits 130. Successful playback still exits 0.
- **Silent pynput hook failures** (#14). Both backends verify the mouse listener actually came up after `start()` and raise a clear error instead of recording nothing; listener teardown is best-effort and never masks the recording's result.
- **Windows: `GetCursorPos` failures returned stale coordinates** (#15). Positions are read into a fresh buffer per call with the `BOOL` return checked (raising `OSError` on failure), and all user32 prototypes are declared.
- **Windows: multi-monitor fail-safe was broken** (#16). Screen metrics now cover the full virtual desktop (`SM_*VIRTUALSCREEN`), a new `get_screen_bounds()` exposes the (possibly negative) origin, corner detection accounts for it, and DPI awareness upgrades to per-monitor-v2 with a legacy fallback.
- **Silent truncation on decode** (#17). `Session.truncated` now reports when a file's event stream stopped early (truncated varint, unknown tag, partial recovery), and `info`/`play`/`export` print a warning for such files.

### Changed (behavior)
- Aborted `play` runs exit nonzero (1 for fail-safe/Esc, 130 for Ctrl-C) instead of 0.
- `record -o` and `export` refuse to overwrite existing files without `--force`.

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
