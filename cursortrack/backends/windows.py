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


def _enable_dpi_awareness(user32: Any) -> None:
    """Request per-monitor DPI awareness, falling back for older Windows.

    SetProcessDpiAwarenessContext requires Windows 10 1703+ and correctly
    reports physical pixels on every monitor. On older releases (or if the
    call is otherwise rejected) fall back to the legacy, primary-monitor-only
    SetProcessDPIAware(), which is still strictly better than leaving DPI
    virtualization on and getting scaled coordinates.
    """
    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class WindowsBackend(InputBackend):
    """Windows-specific implementation using ctypes for emulation/reading and pynput for global hooks."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsBackend can only be initialized on Windows systems.")

        self._user32 = ctypes.windll.user32
        _enable_dpi_awareness(self._user32)

        self._point = POINT()
        self._listener: Any | None = None

    def read_position(self) -> tuple[int, int]:
        self._user32.GetCursorPos(ctypes.byref(self._point))
        return int(self._point.x), int(self._point.y)

    def set_position(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(int(x), int(y))

    def get_screen_size(self) -> tuple[int, int]:
        width = self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return int(width), int(height)

    def get_screen_bounds(self) -> tuple[int, int, int, int]:
        origin_x = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        origin_y = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width, height = self.get_screen_size()
        return int(origin_x), int(origin_y), width, height

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
