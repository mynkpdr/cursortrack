# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
