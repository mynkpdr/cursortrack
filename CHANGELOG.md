# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Playback compatibility preview** (#50): `play --dry-run` reports source/target
  layout and capability mismatches without injecting input.
- **Explicit coordinate mapping** (#50): `--mapping absolute|scale-to-bounds|offset|target-monitor`
  with `--offset-x`/`--offset-y` and `--source-monitor`/`--target-monitor`. Transforms
  are pure and never silently clamp.
- **Strict playback default** (#50): incompatible known layouts, missing button/scroll
  capabilities, invalid button state, and non-complete integrity refuse playback;
  `--permissive` demotes policy refusals to warnings after review.
- Backend `get_layout()` / `get_capabilities()` on Windows and Linux, plus Session
  `source_layout()`, `source_capabilities()`, `button_state_valid`, and
  `layout_metadata_sufficient` helpers.
- Documentation for mapping modes and impossible cross-machine cases in
  [docs/playback-mapping.md](docs/playback-mapping.md).
- Playback-only `--invert-scroll` and `--scroll-scale` controls correct scroll
  direction and intensity without modifying recordings. Fractional scaling
  accumulates remainder so low scales preserve small scroll movements (#63).
- Experimental Windows Precision Touchpad Raw Input capture (#66) reconstructs
  two-finger translation when Chrome, VS Code, or another modern application
  bypasses the legacy global wheel hook. Descriptor-aware
  `doctor --touchpad-test SECONDS` diagnostics fail clearly when capture is
  unavailable or no reconstructed event is observed.

### Changed
- `play` now negotiates compatibility before injection. Same-machine v1/v2 replay
  with matching screen size still works under absolute mapping, with warnings about
  insufficient portable metadata.
- Windows touchpad capture separates pure reconstruction, Win32/HID transport,
  and listener lifecycle code. Programmatic backends use the typed
  `request_enhanced_scroll_capture()`, status, and
  `check_listener_health()` hooks.
- Windows CLI scroll recording automatically requests the compatible native
  source; library callers remain opt-in. Reconstructed input uses existing v2
  integer scroll events, so this introduces no session-format change.

### Fixed
- Compatibility preview now hard-fails unknown buttons and missing button-injection
  support, reports backend restrictions, and catches mapped events outside known
  target bounds before injection.
- Windows labels coordinates as physical pixels only when per-monitor-v2 DPI
  awareness was successfully established; otherwise it reports a backend unit.
- Precision Touchpad startup and teardown now preserve process-wide Raw Input
  ownership, roll back partial startup, keep independent per-device state, and
  report listener/parser loss instead of silently completing with missing scrolls.
- The hook-only Windows fallback preserves `pynput` event coordinates, while
  duplicate arbitration handles out-of-order callbacks and failed delivery
  without retaining phantom scroll debt.

## [0.2.2] - 2026-07-10

A safety and data-integrity release. The v1/v2 on-disk layouts remain
unchanged, but malformed input is now bounded and user-visible behavior is
stricter where continuing could corrupt data or inject unintended input.

### Added
- **Bounded binary decoding** (#41): `DecodeLimits` caps compressed and
  decompressed bytes, event count, frame/coordinate growth, and varint width.
- **Integrity reporting** (#41): `Session.integrity` distinguishes `complete`,
  `truncated`, and `corrupt-recovered` streams while preserving the existing
  `Session.truncated` boolean.
- **Artifact validation** (#47): CI now builds/checks wheel and sdist artifacts,
  verifies PEP 561 metadata, and smoke-tests a minimal wheel installation.

### Fixed
- **Playback fail-safe failure handling** (#38): cursor-read or screen-bounds
  failures now abort instead of silently disabling the fail-safe.
- **Playback cleanup and cancellation** (#38): Esc/corner checks remain active
  during long event gaps, the Esc hook is verified, and injected buttons are
  released on every completion, abort, interrupt, and backend-error path.
- **NumPy overwrite bypass** (#37): implicit `.npy` suffixes are resolved before
  overwrite/same-file checks, and the library exporter writes the exact path it
  receives.
- **Atomic output replacement** (#35): exports and forced recording replacements
  publish with an atomic rename only after successful flush/fsync; failures
  preserve existing destinations.
- **Recorder backend loss** (#36): initial position failure aborts before file
  creation, and mid-session loss returns nonzero with a recoverable truncated
  prefix instead of silently recording stationary data.
- **Recorder timing validation** (#36): quiet mode now honors `--delay`; negative
  or non-finite durations, non-positive flush intervals, and negative delays are
  rejected.
- **Interchange validation** (#39): JSONL/NumPy loaders reject malformed,
  non-finite, out-of-order, unknown, or inconsistent events with source
  locations. Leading blank JSONL lines no longer lose session metadata.
- **CSV safety** (#39): standard quoting preserves embedded commas/newlines and
  formula-like string cells are neutralized for spreadsheet use.
- **False touch capture** (#40): mouse clicks are no longer encoded as
  `TapEvent`; touch-only listener masks are ignored by native mouse backends.

### Changed
- `record --capture touch` now fails with a clear unsupported-capability error.
- `record --capture all` now means all implemented mouse inputs: move, click,
  and scroll. Legacy `CAP_TOUCH`/`TapEvent` files remain readable and playable.
- Binary and interchange loaders now reject invalid or over-limit input rather
  than silently coercing it.

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
