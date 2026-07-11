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
import time
from typing import Any

import pytest

from cursortrack.backends.windows import (
    INPUT,
    INPUT_MOUSE,
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_WHEEL,
    MOUSEEVENTF_XUP,
    POINT,
    SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN,
    WHEEL_DELTA,
    XBUTTON2,
    WindowsBackend,
    _declare_prototypes,
    _enable_dpi_awareness,
)
from cursortrack.core.layout import CoordinateUnit
from tests.conftest import MockBackend

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
            for name in ("GetCursorPos", "SetCursorPos", "GetSystemMetrics", "SendInput")
        },
    )()

    _declare_prototypes(dummy)

    assert dummy.GetCursorPos.restype is ctypes.c_int
    assert dummy.GetCursorPos.argtypes == [ctypes.POINTER(POINT)]
    assert dummy.SetCursorPos.restype is ctypes.c_int
    assert dummy.SetCursorPos.argtypes == [ctypes.c_int, ctypes.c_int]
    assert dummy.GetSystemMetrics.restype is ctypes.c_int
    assert dummy.GetSystemMetrics.argtypes == [ctypes.c_int]
    assert dummy.SendInput.restype is ctypes.c_uint32
    assert dummy.SendInput.argtypes == [
        ctypes.c_uint32,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]


class _FakeSendInput:
    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.events: list[tuple[int, int, int]] = []

    def SendInput(self, count: int, inputs: Any, size: int) -> int:  # noqa: N802
        assert size == ctypes.sizeof(INPUT)
        event_array = ctypes.cast(inputs, ctypes.POINTER(INPUT))
        for index in range(count):
            event = event_array[index]
            self.events.append((event.type, event.mi.dwFlags, event.mi.mouseData))
        return count if self.succeed else 0


def _backend_with_fake_send_input(fake: _FakeSendInput) -> WindowsBackend:
    backend = object.__new__(WindowsBackend)
    backend._user32 = fake
    backend._listener = None
    return backend


def test_click_and_scroll_use_send_input() -> None:
    user32 = _FakeSendInput()
    backend = _backend_with_fake_send_input(user32)

    backend.click("left", True)
    backend.click("x2", False)
    backend.scroll(-2, 3)

    assert user32.events == [
        (INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, 0),
        (INPUT_MOUSE, MOUSEEVENTF_XUP, XBUTTON2),
        (INPUT_MOUSE, MOUSEEVENTF_WHEEL, 3 * WHEEL_DELTA),
        (INPUT_MOUSE, MOUSEEVENTF_HWHEEL, (-2 * WHEEL_DELTA) & 0xFFFFFFFF),
    ]


def test_send_input_failure_is_reported() -> None:
    backend = _backend_with_fake_send_input(_FakeSendInput(succeed=False))

    with pytest.raises(OSError, match="SendInput"):
        backend.click("left", True)


def test_dpi_awareness_is_verified_only_for_per_monitor_v2() -> None:
    modern: Any = type(
        "_Modern",
        (),
        {
            "SetProcessDpiAwarenessContext": lambda _self, _context: 1,
            "SetProcessDPIAware": lambda _self: pytest.fail("fallback should not run"),
        },
    )()
    assert _enable_dpi_awareness(modern) is True

    legacy: Any = type(
        "_Legacy",
        (),
        {
            "SetProcessDpiAwarenessContext": lambda _self, _context: 0,
            "SetProcessDPIAware": lambda _self: 1,
        },
    )()
    assert _enable_dpi_awareness(legacy) is False


def test_layout_claims_physical_pixels_only_after_verified_dpi_awareness() -> None:
    metrics = {
        SM_CXVIRTUALSCREEN: 1920,
        SM_CYVIRTUALSCREEN: 1080,
    }
    user32: Any = type(
        "_Metrics",
        (),
        {"GetSystemMetrics": lambda _self, index: metrics.get(index, 0)},
    )()
    backend = object.__new__(WindowsBackend)
    backend._user32 = user32

    backend._physical_coordinates_verified = False
    uncertain = backend.get_layout()
    assert uncertain.coordinate_unit is CoordinateUnit.BACKEND_UNIT
    assert uncertain.coordinate_unit_id == "win32-desktop-v1"

    backend._physical_coordinates_verified = True
    physical = backend.get_layout()
    assert physical.coordinate_unit is CoordinateUnit.PHYSICAL_PIXEL
    assert physical.coordinate_unit_id is None


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
    backend._listener_error = "previous teardown failed"
    with pytest.raises(RuntimeError, match="hook failed to start"):
        backend.start_listening(lambda *_: None, CAP_CLICK)
    assert backend._listener is None
    assert backend._listener_error == "previous teardown failed"


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
            self.running = False

        def join(self, timeout: float | None = None) -> None:
            calls.append(f"join:{timeout}")

    monkeypatch.setattr(mouse, "Listener", TrackingListener)

    backend = _bare_backend()
    backend.start_listening(lambda *_: None, CAP_CLICK)
    backend.stop_listening()

    assert calls == ["start", "stop", "join:2.0"]
    assert backend._listener is None


def test_base_backend_reports_an_unsupported_enhanced_scroll_request() -> None:
    backend = MockBackend()

    backend.request_enhanced_scroll_capture()
    status = backend.get_enhanced_scroll_capture_status()

    assert status.requested
    assert not status.active
    assert status.degraded_reason == "This backend does not provide enhanced scroll capture."


def test_pynput_cleanup_failure_is_retained_for_health_check() -> None:
    class BrokenListener:
        def stop(self) -> None:
            raise RuntimeError("stop broke")

        def join(self, timeout: float | None = None) -> None:
            del timeout
            raise RuntimeError("join broke")

    backend = _bare_backend()
    backend._listener = BrokenListener()
    backend._touchpad_listener = None
    backend._listener_error = None

    backend.stop_listening()

    with pytest.raises(RuntimeError, match=r"stop broke.*join broke"):
        backend.check_listener_health()


def test_pynput_runtime_failure_remains_visible_after_stop() -> None:
    class DeadListener:
        running = False

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:
            del timeout

    backend = _bare_backend()
    backend._listener = DeadListener()
    backend._touchpad_listener = None
    backend._listener_error = None

    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        backend.check_listener_health()
    backend.stop_listening()
    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        backend.check_listener_health()


def test_pynput_join_timeout_is_reported() -> None:
    class StuckListener:
        running = True

        def stop(self) -> None:
            self.running = False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return True

    backend = _bare_backend()
    backend._listener = StuckListener()
    backend._touchpad_listener = None
    backend._listener_error = None

    backend.stop_listening()

    with pytest.raises(RuntimeError, match="did not stop"):
        backend.check_listener_health()
    assert backend._listener is not None


def test_precision_touchpad_scroll_path_is_deduplicated_and_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw touchpad and synthesized hook events must produce one recorded step."""
    pytest.importorskip("pynput", exc_type=ImportError)
    from pynput import mouse

    import cursortrack.backends.windows as windows_backend
    from cursortrack.core.events import CAP_SCROLL

    calls: list[str] = []
    hook_callbacks: dict[str, Any] = {}
    raw_callbacks: dict[str, Any] = {}

    class TrackingHook:
        running = True

        def __init__(self, **kwargs: object) -> None:
            hook_callbacks.update(kwargs)

        def start(self) -> None:
            calls.append("hook:start")

        def stop(self) -> None:
            calls.append("hook:stop")
            self.running = False

        def join(self, timeout: float | None = None) -> None:
            calls.append(f"hook:join:{timeout}")

    class TrackingTouchpad:
        running = False

        def __init__(self, on_scroll: Any) -> None:
            raw_callbacks["scroll"] = on_scroll

        def start(self) -> bool:
            calls.append("raw:start")
            self.running = True
            return True

        def stop(self) -> None:
            calls.append("raw:stop")
            self.running = False

    monkeypatch.setattr(mouse, "Listener", TrackingHook)
    monkeypatch.setattr(windows_backend, "PrecisionTouchpadScrollListener", TrackingTouchpad)

    backend = _backend_with_fake_user32(_FakeUser32(succeed=True, x=321, y=654))
    backend.request_enhanced_scroll_capture()
    events: list[tuple[str, tuple[Any, ...], float]] = []
    backend.start_listening(lambda *event: events.append(event), CAP_SCROLL)
    active_status = backend.get_enhanced_scroll_capture_status()
    assert active_status.requested
    assert active_status.active
    assert active_status.degraded_reason is None

    timestamp = 10.0
    monkeypatch.setattr(time, "perf_counter", lambda: 10.01)
    raw_callbacks["scroll"](0, -1, timestamp)
    hook_callbacks["on_scroll"](321.0, 654.0, 0.0, -1.0)
    backend.stop_listening()

    assert [(kind, payload) for kind, payload, _ in events] == [("scroll", (321, 654, 0, -1))]
    assert calls == [
        "raw:start",
        "hook:start",
        "hook:stop",
        "hook:join:2.0",
        "raw:stop",
    ]
    assert not backend.get_enhanced_scroll_capture_status().active


def test_hook_only_scroll_preserves_pynput_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled/unavailable raw capture must retain the established hook behavior."""
    pytest.importorskip("pynput", exc_type=ImportError)
    from pynput import mouse

    import cursortrack.backends.windows as windows_backend
    from cursortrack.core.events import CAP_SCROLL

    hook_callbacks: dict[str, Any] = {}

    class TrackingHook:
        running = True

        def __init__(self, **kwargs: object) -> None:
            hook_callbacks.update(kwargs)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            self.running = False

        def join(self, timeout: float | None = None) -> None:
            pass

    class UnavailableTouchpad:
        running = False

        def __init__(self, _on_scroll: Any) -> None:
            pass

        def start(self) -> bool:
            return False

        def stop(self) -> None:
            pass

    monkeypatch.setattr(mouse, "Listener", TrackingHook)
    monkeypatch.setattr(windows_backend, "PrecisionTouchpadScrollListener", UnavailableTouchpad)

    backend = _backend_with_fake_user32(_FakeUser32(succeed=False))
    backend.request_enhanced_scroll_capture()
    events: list[tuple[str, tuple[Any, ...], float]] = []
    backend.start_listening(lambda *event: events.append(event), CAP_SCROLL)
    fallback_status = backend.get_enhanced_scroll_capture_status()
    assert fallback_status.requested
    assert not fallback_status.active
    assert fallback_status.degraded_reason is not None
    hook_callbacks["on_scroll"](123.0, 456.0, 0.0, -1.0)
    backend.stop_listening()

    assert [(kind, payload) for kind, payload, _ in events] == [("scroll", (123, 456, 0, -1))]


def test_hook_start_failure_stops_started_raw_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listener startup is one transaction and cannot leak Raw Input ownership."""
    pytest.importorskip("pynput", exc_type=ImportError)
    from pynput import mouse

    import cursortrack.backends.windows as windows_backend
    from cursortrack.core.events import CAP_SCROLL

    calls: list[str] = []

    class FailingHook:
        running = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("hook:start")
            raise RuntimeError("hook start failed")

        def stop(self) -> None:
            calls.append("hook:stop")

        def join(self, timeout: float | None = None) -> None:
            calls.append(f"hook:join:{timeout}")

    class TrackingTouchpad:
        running = False
        runtime_error = None

        def __init__(self, _on_scroll: Any) -> None:
            pass

        def start(self) -> bool:
            calls.append("raw:start")
            self.running = True
            return True

        def stop(self) -> None:
            calls.append("raw:stop")
            self.running = False

    monkeypatch.setattr(mouse, "Listener", FailingHook)
    monkeypatch.setattr(windows_backend, "PrecisionTouchpadScrollListener", TrackingTouchpad)

    backend = _backend_with_fake_user32(_FakeUser32(succeed=True))
    backend.request_enhanced_scroll_capture()
    with pytest.raises(RuntimeError, match="hook start failed"):
        backend.start_listening(lambda *_: None, CAP_SCROLL)

    assert "raw:stop" in calls
    assert backend._listener is None
    assert backend._touchpad_listener is None


def test_raw_startup_failure_does_not_drop_a_live_thread_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("pynput", exc_type=ImportError)
    from pynput import mouse

    import cursortrack.backends.windows as windows_backend
    from cursortrack.core.events import CAP_SCROLL

    class UnexpectedHook:
        def __init__(self, **_kwargs: object) -> None:
            pytest.fail("pynput must not start after an orphaned raw thread")

    class StuckTouchpad:
        running = False
        thread_alive = True
        runtime_error = "listener did not stop"

        def __init__(self, _on_scroll: Any) -> None:
            pass

        def start(self) -> bool:
            raise RuntimeError("startup timed out")

        def stop(self) -> None:
            pass

    monkeypatch.setattr(mouse, "Listener", UnexpectedHook)
    monkeypatch.setattr(windows_backend, "PrecisionTouchpadScrollListener", StuckTouchpad)

    backend = _backend_with_fake_user32(_FakeUser32(succeed=True))
    backend.request_enhanced_scroll_capture()
    with pytest.raises(RuntimeError, match="could not be stopped"):
        backend.start_listening(lambda *_: None, CAP_SCROLL)

    assert backend._touchpad_listener is not None
    assert backend._listener_error == "listener did not stop"


def test_touchpad_runtime_failure_is_reported_by_listener_health() -> None:
    backend = _bare_backend()
    backend._enhanced_scroll_active = True
    backend._touchpad_listener = type(
        "_FailedTouchpad",
        (),
        {"runtime_error": "malformed HID report", "running": True},
    )()

    with pytest.raises(RuntimeError, match="malformed HID report"):
        backend.check_listener_health()
    assert not backend.get_enhanced_scroll_capture_status().active
    assert backend.get_enhanced_scroll_capture_status().degraded_reason == "malformed HID report"
