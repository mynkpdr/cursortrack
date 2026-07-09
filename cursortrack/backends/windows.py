"""Windows input tracking and emulation backend using native Win32 API (via ctypes) and pynput hooks."""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Callable

from cursortrack.backends.base import InputBackend
from cursortrack.core.events import CAP_CLICK, CAP_SCROLL, CAP_TOUCH

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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _last_error() -> int | None:
    """Return the calling thread's last Win32 error code, or None off-Windows.

    ctypes.get_last_error() only exists on Windows builds of ctypes; guarding
    it lets the failure-path error message stay constructible in the
    cross-platform mock tests for this module.
    """
    getter = getattr(ctypes, "get_last_error", None)
    return getter() if getter is not None else None


def _declare_prototypes(user32: ctypes.WinDLL) -> None:
    """Declare ctypes argument/return types for every user32 call we make.

    Without an explicit BOOL restype, a FALSE return from GetCursorPos could
    be misread past ctypes' default int decoding, and unchecked argtypes
    leave every call silently exposed to 32-/64-bit pointer truncation.
    """
    # Win32 BOOL is a typedef for int; c_int doubles as both here.
    c_bool_ret, c_int, c_ulong = ctypes.c_int, ctypes.c_int, ctypes.c_ulong
    ptr = ctypes.POINTER

    user32.GetCursorPos.restype = c_bool_ret
    user32.GetCursorPos.argtypes = [ptr(POINT)]

    user32.SetCursorPos.restype = c_bool_ret
    user32.SetCursorPos.argtypes = [c_int, c_int]

    user32.GetSystemMetrics.restype = c_int
    user32.GetSystemMetrics.argtypes = [c_int]

    # DWORD dwFlags, dx, dy, dwData; ULONG_PTR dwExtraInfo (pointer-sized, hence c_void_p)
    user32.mouse_event.restype = None
    user32.mouse_event.argtypes = [c_ulong, c_ulong, c_ulong, c_ulong, ctypes.c_void_p]

    if hasattr(user32, "SetProcessDPIAware"):
        user32.SetProcessDPIAware.restype = c_bool_ret
        user32.SetProcessDPIAware.argtypes = []


class WindowsBackend(InputBackend):
    """Windows-specific implementation using ctypes for emulation/reading and pynput for global hooks."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsBackend can only be initialized on Windows systems.")

        self._user32 = ctypes.windll.user32
        _declare_prototypes(self._user32)

        try:
            # Enable DPI Awareness so we retrieve physical pixel positions instead of scaled coordinates
            self._user32.SetProcessDPIAware()
        except Exception:
            pass

        self._listener: Any | None = None

    def read_position(self) -> tuple[int, int]:
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
        width = self._user32.GetSystemMetrics(0)
        height = self._user32.GetSystemMetrics(1)
        return int(width), int(height)

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

        # Emulate click at current cursor position
        self._user32.mouse_event(flags, 0, 0, data, 0)

    def scroll(self, sdx: int, sdy: int) -> None:
        # vertical scroll
        if sdy != 0:
            self._user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(sdy * WHEEL_DELTA), 0)
        # horizontal scroll
        if sdx != 0:
            self._user32.mouse_event(MOUSEEVENTF_HWHEEL, 0, 0, int(sdx * WHEEL_DELTA), 0)

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        # Dynamic import of pynput listener
        try:
            from pynput import mouse
        except ImportError:
            raise ImportError(
                "Capturing click, scroll, or touch events requires 'pynput'. "
                "Install it using 'pip install pynput'."
            )

        import time

        want_click = bool(capture_mask & (CAP_CLICK | CAP_TOUCH))
        want_scroll = bool(capture_mask & CAP_SCROLL)

        if not (want_click or want_scroll):
            return

        def _on_click(x: float, y: float, button: Any, pressed: bool) -> None:
            if want_click:
                on_event("click", (int(x), int(y), button.name, pressed), time.perf_counter())

        def _on_scroll(x: float, y: float, sdx: float, sdy: float) -> None:
            if want_scroll:
                on_event("scroll", (int(x), int(y), int(sdx), int(sdy)), time.perf_counter())

        self._listener = mouse.Listener(
            on_click=_on_click if want_click else None,
            on_scroll=_on_scroll if want_scroll else None,
        )
        self._listener.start()

    def stop_listening(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
