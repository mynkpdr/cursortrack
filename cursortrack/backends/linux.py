"""Linux input tracking and emulation backend using X11 (via ctypes) and pynput hooks."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import sys
from typing import Any, Callable

from cursortrack.backends.base import InputBackend
from cursortrack.core.events import CAP_CLICK, CAP_SCROLL, CAP_TOUCH

# X11 core protocol button numbers
BUTTON_LEFT = 1
BUTTON_MIDDLE = 2
BUTTON_RIGHT = 3
BUTTON_SCROLL_UP = 4
BUTTON_SCROLL_DOWN = 5
BUTTON_SCROLL_LEFT = 6
BUTTON_SCROLL_RIGHT = 7
BUTTON_X1 = 8
BUTTON_X2 = 9

BUTTON_MAP: dict[str, int] = {
    "left": BUTTON_LEFT,
    "middle": BUTTON_MIDDLE,
    "right": BUTTON_RIGHT,
    "x1": BUTTON_X1,
    "x2": BUTTON_X2,
}

# pynput's X11 listener reports side buttons by raw number ("button8"/"button9");
# the canonical "x1"/"x2" names in the session format only exist on Windows.
# Normalize here so recordings stay platform-portable.
PYNPUT_BUTTON_ALIASES: dict[str, str] = {
    "button8": "x1",
    "button9": "x2",
}

# X11 CurrentTime constant (0 = "now" for XTest event injection)
CURRENT_TIME = 0


def _load_x11_library(name: str, fallback: str) -> ctypes.CDLL:
    """Load a shared X11 library by short name, with a conventional soname fallback."""
    path = ctypes.util.find_library(name) or fallback
    try:
        return ctypes.CDLL(path)
    except OSError:
        raise RuntimeError(
            f"Could not load the {fallback} shared library required by the Linux backend. "
            "Install your distribution's X11 client libraries "
            "(e.g. 'sudo apt install libx11-6 libxtst6' on Debian/Ubuntu)."
        )


def _declare_prototypes(xlib: ctypes.CDLL, xtst: ctypes.CDLL) -> None:
    """Declare ctypes argument/return types for every Xlib and XTest call we make.

    Declaring restype is not optional decoration: XOpenDisplay returns a pointer,
    and without an explicit c_void_p restype ctypes truncates it to a 32-bit int
    on 64-bit systems, corrupting every subsequent call.
    """
    c_int, c_uint, c_ulong = ctypes.c_int, ctypes.c_uint, ctypes.c_ulong
    c_void_p, c_char_p = ctypes.c_void_p, ctypes.c_char_p
    ptr = ctypes.POINTER

    xlib.XInitThreads.restype = c_int
    xlib.XInitThreads.argtypes = []

    xlib.XOpenDisplay.restype = c_void_p
    xlib.XOpenDisplay.argtypes = [c_char_p]

    xlib.XCloseDisplay.restype = c_int
    xlib.XCloseDisplay.argtypes = [c_void_p]

    xlib.XDefaultScreen.restype = c_int
    xlib.XDefaultScreen.argtypes = [c_void_p]

    xlib.XRootWindow.restype = c_ulong
    xlib.XRootWindow.argtypes = [c_void_p, c_int]

    xlib.XDisplayWidth.restype = c_int
    xlib.XDisplayWidth.argtypes = [c_void_p, c_int]

    xlib.XDisplayHeight.restype = c_int
    xlib.XDisplayHeight.argtypes = [c_void_p, c_int]

    xlib.XQueryPointer.restype = c_int
    xlib.XQueryPointer.argtypes = [
        c_void_p,  # display
        c_ulong,  # window
        ptr(c_ulong),  # root_return
        ptr(c_ulong),  # child_return
        ptr(c_int),  # root_x_return
        ptr(c_int),  # root_y_return
        ptr(c_int),  # win_x_return
        ptr(c_int),  # win_y_return
        ptr(c_uint),  # mask_return
    ]

    xlib.XWarpPointer.restype = c_int
    xlib.XWarpPointer.argtypes = [
        c_void_p,  # display
        c_ulong,  # src_w
        c_ulong,  # dest_w
        c_int,  # src_x
        c_int,  # src_y
        c_uint,  # src_width
        c_uint,  # src_height
        c_int,  # dest_x
        c_int,  # dest_y
    ]

    xlib.XSync.restype = c_int
    xlib.XSync.argtypes = [c_void_p, c_int]

    xtst.XTestFakeButtonEvent.restype = c_int
    xtst.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]


class LinuxBackend(InputBackend):
    """Linux implementation using Xlib/XTest ctypes for emulation/reading and pynput for hooks.

    Works against any X11 display, including XWayland on Wayland desktops. On
    Wayland, emulation and position reads are scoped to the XWayland surface;
    global capture of events delivered to native Wayland clients is not possible
    from an unprivileged process (see docs/architecture.md).
    """

    def __init__(self) -> None:
        if not sys.platform.startswith("linux"):
            raise RuntimeError("LinuxBackend can only be initialized on Linux systems.")

        self._xlib = _load_x11_library("X11", "libX11.so.6")
        self._xtst = _load_x11_library("Xtst", "libXtst.so.6")
        _declare_prototypes(self._xlib, self._xtst)

        # Must precede any other Xlib call; makes the connection safe to touch from
        # both the recorder's sampling loop and playback fail-safe polling.
        self._xlib.XInitThreads()

        self._display = self._xlib.XOpenDisplay(None)
        if not self._display:
            raise RuntimeError(
                "Could not open an X11 display. CursorTrack on Linux requires a running "
                "X11 (or XWayland) session; check that the DISPLAY environment variable "
                "is set. For headless machines, run under a virtual server such as "
                "'xvfb-run cursortrack ...'."
            )

        self._screen = self._xlib.XDefaultScreen(self._display)
        self._root = self._xlib.XRootWindow(self._display, self._screen)
        self._listener: Any | None = None

    def __del__(self) -> None:
        display = getattr(self, "_display", None)
        if display:
            with contextlib.suppress(Exception):
                self._xlib.XCloseDisplay(display)
            self._display = None

    def read_position(self) -> tuple[int, int]:
        root_return = ctypes.c_ulong()
        child_return = ctypes.c_ulong()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        win_x = ctypes.c_int()
        win_y = ctypes.c_int()
        mask = ctypes.c_uint()
        self._xlib.XQueryPointer(
            self._display,
            self._root,
            ctypes.byref(root_return),
            ctypes.byref(child_return),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(win_x),
            ctypes.byref(win_y),
            ctypes.byref(mask),
        )
        return int(root_x.value), int(root_y.value)

    def _sync(self) -> None:
        # A full round-trip (not just XFlush) is required: flushing the request
        # buffer alone was observed to leave XTest fake events undelivered to
        # other clients' hooks, whereas XSync guarantees the server processed them.
        # pynput's own Linux controller syncs after every injection for the same reason.
        self._xlib.XSync(self._display, 0)

    def set_position(self, x: int, y: int) -> None:
        self._xlib.XWarpPointer(self._display, 0, self._root, 0, 0, 0, 0, int(x), int(y))
        self._sync()

    def get_screen_size(self) -> tuple[int, int]:
        width = self._xlib.XDisplayWidth(self._display, self._screen)
        height = self._xlib.XDisplayHeight(self._display, self._screen)
        return int(width), int(height)

    def click(self, button: str, pressed: bool) -> None:
        # Unknown button names are a no-op: substituting a left click (the old
        # fallback) performs a real, potentially destructive action the user
        # never recorded.
        x_button = BUTTON_MAP.get(button.lower())
        if x_button is None:
            return
        self._xtst.XTestFakeButtonEvent(self._display, x_button, int(pressed), CURRENT_TIME)
        self._sync()

    def _tap_button(self, x_button: int) -> None:
        self._xtst.XTestFakeButtonEvent(self._display, x_button, 1, CURRENT_TIME)
        self._xtst.XTestFakeButtonEvent(self._display, x_button, 0, CURRENT_TIME)

    def scroll(self, sdx: int, sdy: int) -> None:
        # X11 core protocol has no scroll-delta events: each wheel "step" is a
        # press+release of buttons 4-7.
        if sdy != 0:
            x_button = BUTTON_SCROLL_UP if sdy > 0 else BUTTON_SCROLL_DOWN
            for _ in range(abs(int(sdy))):
                self._tap_button(x_button)
        if sdx != 0:
            x_button = BUTTON_SCROLL_RIGHT if sdx > 0 else BUTTON_SCROLL_LEFT
            for _ in range(abs(int(sdx))):
                self._tap_button(x_button)
        if sdx != 0 or sdy != 0:
            self._sync()

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        # Dynamic import of pynput listener (uses X11 hooks on Linux)
        try:
            from pynput import mouse
        except ImportError:
            raise ImportError(
                "Capturing click, scroll, or touch events requires 'pynput'. "
                "Install it using 'pip install pynput' or 'pip install cursortrack[linux]'."
            )

        import time

        want_click = bool(capture_mask & (CAP_CLICK | CAP_TOUCH))
        want_scroll = bool(capture_mask & CAP_SCROLL)

        if not (want_click or want_scroll):
            return

        def _on_click(x: float, y: float, button: Any, pressed: bool) -> None:
            if want_click:
                name = PYNPUT_BUTTON_ALIASES.get(button.name, button.name)
                on_event("click", (int(x), int(y), name, pressed), time.perf_counter())

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
