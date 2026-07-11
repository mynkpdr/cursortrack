"""Windows input tracking and emulation backend using native Win32 API (via ctypes) and pynput hooks."""

from __future__ import annotations

import contextlib
import ctypes
import sys
import warnings
from typing import Any, Callable

from cursortrack.backends._pynput_listener import verify_listener_running
from cursortrack.backends._touchpad_scroll import ScrollEventArbiter
from cursortrack.backends._windows_touchpad import (
    PrecisionTouchpadScrollListener,
    windows_touchpad_capture_enabled,
)
from cursortrack.backends.base import EnhancedScrollCaptureStatus, InputBackend
from cursortrack.core.events import CAP_CLICK, CAP_SCROLL
from cursortrack.core.layout import (
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    MonitorLayout,
    Rect,
    ScrollUnit,
)

# Win32 Mouse Constants
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

# dwData values selecting which extended button MOUSEEVENTF_XDOWN/XUP refers to
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

WHEEL_DELTA = 120
INPUT_MOUSE = 0

# GetSystemMetrics indices for the virtual desktop (the bounding box of *all*
# monitors). Indices 0/1 (SM_CXSCREEN/SM_CYSCREEN) only cover the primary
# monitor, which breaks bounds/fail-safe checks as soon as a secondary
# monitor extends the desktop beyond it.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, passed to
# SetProcessDpiAwarenessContext (Windows 10 1703+). Per-monitor-v2 awareness
# reports true physical pixels on every monitor regardless of its DPI scale;
# the legacy SetProcessDPIAware() fallback below only does so for whichever
# monitor was active at process startup.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MOUSEINPUT(ctypes.Structure):
    """Exact Win32 MOUSEINPUT layout on both 32- and 64-bit hosts."""

    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]  # noqa: RUF012 - ctypes requires a class-level schema


class INPUT(ctypes.Structure):
    """Win32 INPUT union containing the mouse event variant used here."""

    _anonymous_ = ("value",)
    _fields_ = [("type", ctypes.c_uint32), ("value", _INPUTUNION)]


def _last_error() -> int | None:
    """Return the calling thread's last Win32 error code, or None off-Windows.

    ctypes.get_last_error() only exists on Windows builds of ctypes; guarding
    it lets the failure-path error message stay constructible in the
    cross-platform mock tests for this module.
    """
    getter = getattr(ctypes, "get_last_error", None)
    return getter() if getter is not None else None


def _listener_thread_alive(listener: Any) -> bool:
    """Read actual thread liveness, falling back for compatible test doubles."""
    is_alive = getattr(listener, "is_alive", None)
    if callable(is_alive):
        return bool(is_alive())
    return bool(getattr(listener, "running", False))


def _declare_prototypes(user32: Any) -> None:
    """Declare ctypes argument/return types for every user32 call we make.

    Without an explicit BOOL restype, a FALSE return from GetCursorPos could
    be misread past ctypes' default int decoding, and unchecked argtypes
    leave every call silently exposed to 32-/64-bit pointer truncation.
    """
    # Win32 BOOL is a typedef for int; c_int doubles as both here.
    c_bool_ret, c_int = ctypes.c_int, ctypes.c_int
    ptr = ctypes.POINTER

    user32.GetCursorPos.restype = c_bool_ret
    user32.GetCursorPos.argtypes = [ptr(POINT)]

    user32.SetCursorPos.restype = c_bool_ret
    user32.SetCursorPos.argtypes = [c_int, c_int]

    user32.GetSystemMetrics.restype = c_int
    user32.GetSystemMetrics.argtypes = [c_int]

    user32.SendInput.restype = ctypes.c_uint32
    user32.SendInput.argtypes = [ctypes.c_uint32, ptr(INPUT), c_int]

    if hasattr(user32, "SetProcessDpiAwarenessContext"):
        # BOOL SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT) - the
        # context is a pointer-sized pseudo-handle, hence c_void_p.
        user32.SetProcessDpiAwarenessContext.restype = c_bool_ret
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]

    if hasattr(user32, "SetProcessDPIAware"):
        user32.SetProcessDPIAware.restype = c_bool_ret
        user32.SetProcessDPIAware.argtypes = []


def _enable_dpi_awareness(user32: Any) -> bool:
    """Request per-monitor DPI awareness, falling back for older Windows.

    SetProcessDpiAwarenessContext requires Windows 10 1703+ and correctly
    reports physical pixels on every monitor. On older releases (or if the
    call is otherwise rejected) fall back to the legacy, primary-monitor-only
    SetProcessDPIAware(), which is still strictly better than leaving DPI
    virtualization on and getting scaled coordinates.

    Returns:
        True only when per-monitor-v2 awareness was established and physical
        coordinate metadata can be advertised with confidence.
    """
    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return True
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    return False


class WindowsBackend(InputBackend):
    """Windows-specific implementation using ctypes for emulation/reading and pynput for global hooks."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsBackend can only be initialized on Windows systems.")

        self._user32 = ctypes.windll.user32
        _declare_prototypes(self._user32)
        self._physical_coordinates_verified = _enable_dpi_awareness(self._user32)

        self._listener: Any | None = None
        self._touchpad_listener: PrecisionTouchpadScrollListener | None = None
        self._scroll_arbiter: ScrollEventArbiter | None = None
        self._enhanced_scroll_requested = False
        self._enhanced_scroll_active = False
        self._enhanced_scroll_degraded_reason: str | None = None
        self._listener_error: str | None = None

    def request_enhanced_scroll_capture(self) -> None:
        """Opt into process-wide Precision Touchpad Raw Input capture."""
        self._enhanced_scroll_requested = True

    def get_enhanced_scroll_capture_status(self) -> EnhancedScrollCaptureStatus:
        return EnhancedScrollCaptureStatus(
            requested=getattr(self, "_enhanced_scroll_requested", False),
            active=getattr(self, "_enhanced_scroll_active", False),
            degraded_reason=getattr(self, "_enhanced_scroll_degraded_reason", None),
        )

    def read_position(self) -> tuple[int, int]:
        # A fresh POINT per call: a shared instance-level buffer risked handing
        # out a previous call's coordinates on failure, and torn reads if two
        # threads ever queried through the same backend.
        point = POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            raise OSError(
                f"GetCursorPos failed (error {_last_error()}); "
                "refusing to return a stale cursor position."
            )
        return int(point.x), int(point.y)

    def set_position(self, x: int, y: int) -> None:
        if not self._user32.SetCursorPos(int(x), int(y)):
            raise OSError(f"SetCursorPos({x}, {y}) failed (error {_last_error()}).")

    def get_screen_size(self) -> tuple[int, int]:
        width = self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return int(width), int(height)

    def get_screen_bounds(self) -> tuple[int, int, int, int]:
        origin_x = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        origin_y = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width, height = self.get_screen_size()
        return int(origin_x), int(origin_y), width, height

    def get_layout(self) -> DesktopLayout:
        origin_x, origin_y, width, height = self.get_screen_bounds()
        physical = getattr(self, "_physical_coordinates_verified", False)
        coordinate_unit = CoordinateUnit.PHYSICAL_PIXEL if physical else CoordinateUnit.BACKEND_UNIT
        coordinate_unit_id = None if physical else "win32-desktop-v1"
        if width <= 0 or height <= 0:
            return DesktopLayout.unknown(coordinate_unit, coordinate_unit_id)
        bounds = Rect(origin_x, origin_y, width, height)
        return DesktopLayout(
            known=True,
            coordinate_unit=coordinate_unit,
            coordinate_unit_id=coordinate_unit_id,
            bounds=bounds,
            monitors=(MonitorLayout(id="virtual-desktop", primary=True, bounds=bounds),),
        )

    def get_capabilities(self) -> InputCapabilities:
        layout = self.get_layout()
        return InputCapabilities(
            coordinate_unit=layout.coordinate_unit,
            coordinate_unit_id=layout.coordinate_unit_id,
            buttons=("left", "right", "middle", "x1", "x2"),
            scroll_units=(ScrollUnit.WHEEL_DETENT,),
            precise_scroll=False,
            read_position=True,
            inject_position=True,
            inject_buttons=True,
            inject_scroll=True,
            capture_buttons=True,
            capture_scroll=True,
            restrictions=("interactive-desktop-only",),
        )

    def _send_mouse_inputs(self, events: list[tuple[int, int]]) -> None:
        """Inject checked mouse events, preserving signed wheel data as DWORD bits."""
        inputs = (INPUT * len(events))()
        for index, (flags, mouse_data) in enumerate(events):
            inputs[index].type = INPUT_MOUSE
            inputs[index].mi.mouseData = mouse_data & 0xFFFFFFFF
            inputs[index].mi.dwFlags = flags

        sent = self._user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise OSError(
                f"SendInput injected {sent} of {len(inputs)} mouse events "
                f"(error {_last_error()}); input may be blocked by Windows UIPI."
            )

    def click(self, button: str, pressed: bool) -> None:
        btn = button.lower()
        data = 0
        if btn == "left":
            flags = MOUSEEVENTF_LEFTDOWN if pressed else MOUSEEVENTF_LEFTUP
        elif btn == "right":
            flags = MOUSEEVENTF_RIGHTDOWN if pressed else MOUSEEVENTF_RIGHTUP
        elif btn == "middle":
            flags = MOUSEEVENTF_MIDDLEDOWN if pressed else MOUSEEVENTF_MIDDLEUP
        elif btn == "x1":
            flags = MOUSEEVENTF_XDOWN if pressed else MOUSEEVENTF_XUP
            data = XBUTTON1
        elif btn == "x2":
            flags = MOUSEEVENTF_XDOWN if pressed else MOUSEEVENTF_XUP
            data = XBUTTON2
        else:
            # Unknown button names are a no-op: substituting a left click (the
            # old fallback) performs a real, potentially destructive action the
            # user never recorded.
            return

        self._send_mouse_inputs([(flags, data)])

    def scroll(self, sdx: int, sdy: int) -> None:
        events: list[tuple[int, int]] = []
        if sdy != 0:
            events.append((MOUSEEVENTF_WHEEL, int(sdy * WHEEL_DELTA)))
        if sdx != 0:
            events.append((MOUSEEVENTF_HWHEEL, int(sdx * WHEEL_DELTA)))
        if events:
            self._send_mouse_inputs(events)

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        want_click = bool(capture_mask & CAP_CLICK)
        want_scroll = bool(capture_mask & CAP_SCROLL)
        if not (want_click or want_scroll):
            return
        if (
            getattr(self, "_listener", None) is not None
            or getattr(self, "_touchpad_listener", None) is not None
        ):
            raise RuntimeError("Windows input listeners are already active.")

        self._touchpad_listener = None
        self._scroll_arbiter = None
        self._enhanced_scroll_active = False
        self._enhanced_scroll_degraded_reason = None

        # Dynamic import of pynput listener
        try:
            from pynput import mouse
        except ImportError:
            raise ImportError(
                "Capturing click or scroll events requires 'pynput'. "
                "Install it using 'pip install cursortrack[windows]'."
            )

        import time

        def _emit_scroll(_source: str, sdx: int, sdy: int, timestamp: float) -> None:
            x, y = self.read_position()
            on_event("scroll", (x, y, sdx, sdy), timestamp)

        def _on_raw_scroll(sdx: int, sdy: int, timestamp: float) -> None:
            arbiter = self._scroll_arbiter
            if arbiter is not None:
                arbiter.emit_raw(sdx, sdy, timestamp)

        def _on_click(x: float, y: float, button: Any, pressed: bool) -> None:
            if want_click:
                on_event("click", (int(x), int(y), button.name, pressed), time.perf_counter())

        def _on_scroll(x: float, y: float, sdx: float, sdy: float) -> None:
            timestamp = time.perf_counter()
            arbiter = self._scroll_arbiter
            if arbiter is None:
                on_event(
                    "scroll",
                    (int(x), int(y), int(sdx), int(sdy)),
                    timestamp,
                )
                return
            arbiter.emit_hook(int(sdx), int(sdy), timestamp)

        try:
            enhanced_scroll_enabled = want_scroll and windows_touchpad_capture_enabled(
                getattr(self, "_enhanced_scroll_requested", False),
            )
            if want_scroll and enhanced_scroll_enabled:
                self._enhanced_scroll_requested = True
                self._scroll_arbiter = ScrollEventArbiter(_emit_scroll)
                touchpad_listener = PrecisionTouchpadScrollListener(_on_raw_scroll)
                self._touchpad_listener = touchpad_listener
                try:
                    if not touchpad_listener.start():
                        self._touchpad_listener = None
                        self._scroll_arbiter = None
                        self._enhanced_scroll_degraded_reason = (
                            "No compatible enabled Precision Touchpad is available."
                        )
                    else:
                        self._enhanced_scroll_active = True
                except ValueError:
                    raise
                except Exception as error:
                    with contextlib.suppress(Exception):
                        touchpad_listener.stop()
                    thread_alive = getattr(
                        touchpad_listener,
                        "thread_alive",
                        touchpad_listener.running,
                    )
                    if not thread_alive:
                        self._touchpad_listener = None
                    else:
                        raise RuntimeError(
                            "Precision Touchpad startup failed and its listener "
                            f"thread could not be stopped: {error}"
                        ) from error
                    self._scroll_arbiter = None
                    self._enhanced_scroll_degraded_reason = str(error)
                    warnings.warn(
                        "Precision Touchpad raw capture could not start; "
                        f"using the standard wheel hook only: {error}",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            elif want_scroll and getattr(self, "_enhanced_scroll_requested", False):
                self._enhanced_scroll_degraded_reason = (
                    "Enhanced scroll capture is disabled by CURSORTRACK_WINDOWS_TOUCHPAD."
                )

            self._listener = mouse.Listener(
                on_click=_on_click if want_click else None,
                on_scroll=_on_scroll if want_scroll else None,
            )
            self._listener.start()
            verify_listener_running(
                self._listener,
                "The pynput mouse hook failed to start. Check that no "
                "security software is blocking the low-level mouse hook and "
                "that the process has permission to install one.",
            )
            self._listener_error = None
        except BaseException:
            self.stop_listening()
            raise

    def stop_listening(self) -> None:
        # Mirror the Linux backend: pynput's stop()/join() can raise during
        # listener teardown races, and detecting a failed *start* is the point
        # of #14, so stop must be best-effort rather than propagating errors.
        if self._listener is not None:
            cleanup_errors: list[str] = []
            try:
                self._listener.stop()
            except Exception as error:
                cleanup_errors.append(f"stop failed: {error}")
            try:
                self._listener.join(timeout=2.0)
            except Exception as error:
                cleanup_errors.append(f"join failed: {error}")
            listener_alive = _listener_thread_alive(self._listener)
            if listener_alive:
                cleanup_errors.append("listener did not stop within 2 seconds")
            if cleanup_errors and getattr(self, "_listener_error", None) is None:
                self._listener_error = "pynput listener cleanup failed: " + "; ".join(
                    cleanup_errors
                )
            if not listener_alive:
                self._listener = None
        if self._touchpad_listener is not None:
            with contextlib.suppress(Exception):
                self._touchpad_listener.stop()
            runtime_error = getattr(self._touchpad_listener, "runtime_error", None)
            if runtime_error is not None:
                self._listener_error = runtime_error
                self._enhanced_scroll_degraded_reason = runtime_error
            thread_alive = getattr(
                self._touchpad_listener,
                "thread_alive",
                self._touchpad_listener.running,
            )
            if not thread_alive:
                self._touchpad_listener = None
        self._enhanced_scroll_active = False
        self._scroll_arbiter = None

    def check_listener_health(self) -> None:
        """Raise if an enabled native listener has failed or stopped."""
        error = getattr(self, "_listener_error", None)
        if error is not None:
            raise RuntimeError(error)
        mouse_listener = getattr(self, "_listener", None)
        if mouse_listener is not None:
            listener_running = bool(getattr(mouse_listener, "running", True))
            if not listener_running or not _listener_thread_alive(mouse_listener):
                message = "pynput mouse listener stopped unexpectedly."
                self._listener_error = message
                raise RuntimeError(message)
        touchpad_listener = getattr(self, "_touchpad_listener", None)
        if touchpad_listener is None:
            return
        check_health = getattr(touchpad_listener, "check_health", None)
        runtime_error = (
            check_health()
            if callable(check_health)
            else getattr(touchpad_listener, "runtime_error", None)
        )
        if runtime_error is not None:
            self._enhanced_scroll_active = False
            self._enhanced_scroll_degraded_reason = runtime_error
            raise RuntimeError(runtime_error)
        if not touchpad_listener.running:
            message = "Precision Touchpad listener stopped unexpectedly."
            self._enhanced_scroll_active = False
            self._enhanced_scroll_degraded_reason = message
            raise RuntimeError(message)
