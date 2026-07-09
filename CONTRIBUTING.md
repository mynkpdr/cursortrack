# Contributing to CursorTrack

We welcome contributions to CursorTrack! This document outlines coding standards, tests, and steps to implement new operating system backends.

## Developer Setup

1. Clone this repository to your machine.
2. Install CursorTrack in editable mode with development dependencies:
   ```bash
   pip install -e .[dev,zstd,ml]
   ```

## Code Quality Standards

We enforce strict linting, formatting, and typing policies to keep the codebase clean and robust:

- **Formatting & Linting**: We use **Ruff** for formatting and linting. Run validation before committing:
  ```bash
  ruff check .
  ruff format --check .
  ```
- **Type Safety**: The project uses strict **mypy** settings (configured in `pyproject.toml`, so no `--strict` flag is needed on the command line — passing it explicitly overrides settings like `warn_unused_ignores` and produces different results). Run type-check validation:
  ```bash
  mypy cursortrack
  ```
- **Testing**: Ensure that all tests pass, and write tests for any new features or core logic:
  ```bash
  pytest
  ```

---

## Adding a New OS Backend

CursorTrack isolates all OS-specific routines behind the `InputBackend` abstract interface. This makes adding Linux or macOS support entirely additive without modifying CLI commands or file format serialization logic.

### Step 1: Implement the Backend Subclass
Create or modify the backend file, e.g. `cursortrack/backends/macos.py` (the Windows and Linux backends in the same directory are good reference implementations). Implement all required methods from the `InputBackend` class:

- `read_position()`: Return `(x, y)` coordinate tuple.
- `set_position(x, y)`: Move the physical cursor.
- `get_screen_size()`: Return `(width, height)` tuple.
- `click(button, pressed)`: Emulate button clicks.
- `scroll(sdx, sdy)`: Emulate scroll wheel events.
- `start_listening(on_event, capture_mask)`: Establish global background hooks (e.g. using `pynput` or platform native utilities like Quartz or Xlib).
- `stop_listening()`: Cleanup hooks.

### Step 2: Update Package Requirements
If your backend introduces new platform-specific libraries:
1. Add them as optional dependencies in `pyproject.toml` (e.g. `linux = ["pynput>=1.8.0"]` or `macos = ["pyobjc-framework-Quartz"]`).
2. Make sure you don't import these dependencies globally in `cursortrack/backends/your_os.py` if doing so would break imports on other platforms (Windows, etc.). Use dynamic, runtime imports inside the listening/emulation methods.

### Step 3: Register the Backend Class
Open [cursortrack/backends/\_\_init\_\_.py](cursortrack/backends/__init__.py) and add your backend target mappings:

```python
from cursortrack.backends.macos import MacOSBackend

BACKEND_CLASSES: dict[str, type[InputBackend]] = {
    "win": WindowsBackend,
    "linux": LinuxBackend,
    "macos": MacOSBackend,  # Now registers your custom class!
}
```

### Step 4: Add Unit Tests
Add verification coverage inside `tests/` using mocked interfaces or dummy setups to ensure tests run automatically on GitHub Actions CI. If your backend can be exercised headlessly (as Linux is via `xvfb-run`), add real integration tests that skip gracefully when the required display/permissions are unavailable — see `tests/test_linux_backend.py` for the pattern.
