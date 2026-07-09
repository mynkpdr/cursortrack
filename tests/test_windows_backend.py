"""Tests for the Windows Win32/pynput backend.

The real backend requires the Win32 API (via `ctypes.windll`) and can only be
constructed on Windows, so integration tests are skipped elsewhere. The
mocked dead-listener test below bypasses `WindowsBackend.__init__` (which
never touches `ctypes.windll`) so it can run on every platform.
"""

from __future__ import annotations

import sys

import pytest

from cursortrack.backends.windows import WindowsBackend
from cursortrack.core.events import CAP_CLICK

IS_WINDOWS = sys.platform.startswith("win")

requires_windows = pytest.mark.skipif(not IS_WINDOWS, reason="Requires a real Windows session.")


def _bare_backend() -> WindowsBackend:
    """Build a WindowsBackend without running __init__ (which needs ctypes.windll)."""
    backend = WindowsBackend.__new__(WindowsBackend)
    backend._listener = None
    return backend


@requires_windows
def test_listener_is_running_after_start_listening() -> None:
    """A successfully started listener must report `running` and stop cleanly.

    Regression test for #14: start_listening() used to return without ever
    checking whether pynput's low-level mouse hook actually came up, so a
    failed hook install would record silently instead of raising.
    """
    pytest.importorskip("pynput")

    backend = WindowsBackend()
    backend.start_listening(lambda *_: None, CAP_CLICK)
    try:
        assert backend._listener is not None
        assert backend._listener.running
    finally:
        backend.stop_listening()

    assert backend._listener is None


def test_start_listening_raises_when_hook_never_comes_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead listener thread must raise a clear RuntimeError, not record silently.

    Regression test for #14. Mocks pynput.mouse.Listener directly, so this
    runs on every platform (no Win32 API or real hook install needed).
    """
    # exc_type=ImportError (not just ModuleNotFoundError): pynput's own
    # import can fail outright on a Linux box with no X display, which is an
    # environment gap, not a regression to report as a test failure here.
    pytest.importorskip("pynput", exc_type=ImportError)
    from pynput import mouse

    class DeadListener:
        running = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass  # simulates a hook install that silently fails

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            pass

    monkeypatch.setattr(mouse, "Listener", DeadListener)

    backend = _bare_backend()
    with pytest.raises(RuntimeError, match="hook failed to start"):
        backend.start_listening(lambda *_: None, CAP_CLICK)
    assert backend._listener is None


def test_stop_listening_joins_a_live_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop_listening must stop and join the listener thread before clearing it."""
    pytest.importorskip("pynput", exc_type=ImportError)
    from pynput import mouse

    calls: list[str] = []

    class TrackingListener:
        running = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

        def join(self, timeout: float | None = None) -> None:
            calls.append(f"join:{timeout}")

    monkeypatch.setattr(mouse, "Listener", TrackingListener)

    backend = _bare_backend()
    backend.start_listening(lambda *_: None, CAP_CLICK)
    backend.stop_listening()

    assert calls == ["start", "stop", "join:2.0"]
    assert backend._listener is None
