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


class WindowsBackend(InputBackend):
    """Windows-specific implementation using ctypes for emulation/reading and pynput for global hooks."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsBackend can only be initialized on Windows systems.")

        try:
            # Enable DPI Awareness so we retrieve physical pixel positions instead of scaled coordinates
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

        self._user32 = ctypes.windll.user32
        self._point = POINT()
        self._listener: Any | None = None

    def read_position(self) -> tuple[int, int]:
        self._user32.GetCursorPos(ctypes.byref(self._point))
        return int(self._point.x), int(self._point.y)

    def set_position(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(int(x), int(y))

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
