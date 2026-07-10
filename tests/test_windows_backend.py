"""Tests for the Windows Win32 (ctypes) backend.

Real GetCursorPos/SetCursorPos round-trips and virtual-screen metrics checks
only run on Windows (ctypes.windll and user32 exist nowhere else). The
GetCursorPos-failure regression tests build a WindowsBackend without running
__init__ (which requires loading user32.dll), so they - and the
prototype-declaration checks - run on every platform.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

import pytest

from cursortrack.backends.windows import POINT, WindowsBackend, _declare_prototypes

IS_WINDOWS = sys.platform.startswith("win")

requires_windows = pytest.mark.skipif(
    not IS_WINDOWS, reason="Requires a real Win32 user32.dll to exercise the backend."
)


class _FakeUser32:
    """Stand-in for ctypes.windll.user32 that lets tests control GetCursorPos's outcome."""

    def __init__(self, succeed: bool, x: int = 0, y: int = 0) -> None:
        self.succeed = succeed
        self.x = x
        self.y = y

    def GetCursorPos(self, point_ref: Any) -> int:  # noqa: N802 - must match the real Win32 API name
        if not self.succeed:
            return 0
        point = ctypes.cast(point_ref, ctypes.POINTER(POINT)).contents
        point.x, point.y = self.x, self.y
        return 1

    def SetCursorPos(self, _x: int, _y: int) -> int:  # noqa: N802 - must match the real Win32 API name
        return 1 if self.succeed else 0


def _backend_with_fake_user32(fake: _FakeUser32) -> WindowsBackend:
    """Build a WindowsBackend without running __init__ (which requires Windows)."""
    backend = object.__new__(WindowsBackend)
    backend._user32 = fake
    backend._listener = None
    return backend


def test_failed_get_cursor_pos_raises_instead_of_returning_stale_position() -> None:
    """GetCursorPos returning FALSE must raise, not silently hand back garbage/stale coords.

    Regression test for #15: the old code ignored the BOOL return value of
    GetCursorPos entirely.
    """
    backend = _backend_with_fake_user32(_FakeUser32(succeed=False))
    with pytest.raises(OSError):
        backend.read_position()


def test_failed_set_cursor_pos_raises() -> None:
    backend = _backend_with_fake_user32(_FakeUser32(succeed=False))
    with pytest.raises(OSError):
        backend.set_position(10, 10)


def test_successful_get_cursor_pos_returns_the_written_coordinates() -> None:
    backend = _backend_with_fake_user32(_FakeUser32(succeed=True, x=123, y=456))
    assert backend.read_position() == (123, 456)


def test_read_position_does_not_reuse_an_instance_level_point_buffer() -> None:
    """Regression test for #15: a shared self._point risked handing out a
    previous call's coordinates if a later GetCursorPos call failed partway."""
    backend = _backend_with_fake_user32(_FakeUser32(succeed=True, x=1, y=2))
    backend.read_position()
    assert "_point" not in backend.__dict__


def test_declare_prototypes_sets_explicit_bool_restype_for_position_calls() -> None:
    """GetCursorPos/SetCursorPos need an explicit BOOL restype so a FALSE
    return is preserved instead of falling back to ctypes' default int decoding.
    """
    dummy: Any = type(
        "_Dummy",
        (),
        {
            name: type(name, (), {})()
            for name in ("GetCursorPos", "SetCursorPos", "GetSystemMetrics", "mouse_event")
        },
    )()

    _declare_prototypes(dummy)

    assert dummy.GetCursorPos.restype is ctypes.c_int
    assert dummy.GetCursorPos.argtypes == [ctypes.POINTER(POINT)]
    assert dummy.SetCursorPos.restype is ctypes.c_int
    assert dummy.SetCursorPos.argtypes == [ctypes.c_int, ctypes.c_int]
    assert dummy.GetSystemMetrics.restype is ctypes.c_int
    assert dummy.GetSystemMetrics.argtypes == [ctypes.c_int]
    assert dummy.mouse_event.argtypes[:4] == [ctypes.c_ulong] * 4


# --- Real Win32 tests: skip everywhere but Windows ---------------------------


@requires_windows
def test_virtual_screen_metrics_are_positive() -> None:
    backend = WindowsBackend()
    width, height = backend.get_screen_size()
    assert width > 0
    assert height > 0


@requires_windows
def test_screen_bounds_are_consistent_with_screen_size() -> None:
    backend = WindowsBackend()
    ox, oy, w, h = backend.get_screen_bounds()
    assert (w, h) == backend.get_screen_size()
    assert isinstance(ox, int)
    assert isinstance(oy, int)


@requires_windows
def test_set_and_read_position_round_trip() -> None:
    backend = WindowsBackend()
    backend.set_position(123, 217)
    assert backend.read_position() == (123, 217)
    backend.set_position(300, 40)
    assert backend.read_position() == (300, 40)


# --- Listener hook-failure detection (#14) -----------------------------------


def _bare_backend() -> WindowsBackend:
    """Build a WindowsBackend without running __init__ (which needs ctypes.windll)."""
    backend = object.__new__(WindowsBackend)
    backend._listener = None
    return backend


def test_touch_only_mask_does_not_install_a_mouse_listener() -> None:
    """Reserved touch events must not be synthesized from ordinary mouse clicks."""
    from cursortrack.core.events import CAP_TOUCH

    backend = _bare_backend()
    backend.start_listening(lambda *_: None, CAP_TOUCH)

    assert backend._listener is None


@requires_windows
def test_listener_is_running_after_start_listening() -> None:
    """A successfully started listener must report `running` and stop cleanly.

    Regression test for #14: start_listening() used to return without ever
    checking whether pynput's low-level mouse hook actually came up, so a
    failed hook install would record silently instead of raising.
    """
    pytest.importorskip("pynput")
    from cursortrack.core.events import CAP_CLICK

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

    from cursortrack.core.events import CAP_CLICK

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

    from cursortrack.core.events import CAP_CLICK

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
