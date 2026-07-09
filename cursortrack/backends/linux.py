"""Linux input tracking and emulation backend using X11 (via ctypes) and pynput hooks."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import sys
from typing import Any, Callable

from cursortrack.backends._pynput_listener import verify_listener_running
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


class _XErrorEvent(ctypes.Structure):
    """Layout of XErrorEvent from X11/Xlib.h (verified against upstream libX11)."""

    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resourceid", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


_X_ERROR_HANDLER_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent)
)
_X_IO_ERROR_HANDLER_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_X_IO_ERROR_EXIT_HANDLER_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

#: Display pointers whose X server connection has been lost. Checked by every
#: backend method so a dead connection raises instead of misbehaving.
_DEAD_DISPLAYS: set[int] = set()

_protocol_errors_reported: set[tuple[int, int]] = set()

# The callback objects must stay referenced for the process lifetime: if Python
# garbage-collects them, libX11 would call into freed memory.
_error_handler_ref: Any = None
_io_error_handler_ref: Any = None
_io_exit_handler_ref: Any = None


@_X_ERROR_HANDLER_TYPE  # type: ignore[untyped-decorator, misc]
def _on_x_protocol_error(_display: int, event: Any) -> int:
    """Log non-fatal X protocol errors once per kind and keep running.

    Xlib's *default* handler prints a message and then calls exit(), which
    would kill the whole process (discarding any recording still buffered in
    memory) over conditions as mundane as a BadWindow race.
    """
    ev = event.contents
    key = (int(ev.error_code), int(ev.request_code))
    if key not in _protocol_errors_reported:
        _protocol_errors_reported.add(key)
        sys.stderr.write(
            f"cursortrack: ignoring X protocol error (error_code={ev.error_code}, "
            f"request_code={ev.request_code}, minor_code={ev.minor_code})\n"
        )
    return 0


@_X_IO_ERROR_HANDLER_TYPE  # type: ignore[untyped-decorator, misc]
def _on_x_io_error(display: int) -> int:
    """Record the dead connection in place of the default fatal-IO handler.

    The *default* handler prints "XIO: fatal IO error" and calls exit(1)
    itself. Replacing it lets _XIOError proceed to the per-display exit
    handler below, which is what actually prevents process termination.
    """
    if display:
        _DEAD_DISPLAYS.add(display)
    return 0


@_X_IO_ERROR_EXIT_HANDLER_TYPE  # type: ignore[untyped-decorator, misc]
def _on_x_io_error_exit(display: int, _user_data: int) -> None:
    """Mark the display dead instead of letting Xlib exit() the process.

    Registered via XSetIOErrorExitHandler (libX11 >= 1.6.9): on a fatal IO
    error (X server gone, connection dropped) Xlib invokes this and the
    failing call returns to the caller, rather than terminating Python.
    """
    if display:
        _DEAD_DISPLAYS.add(display)


def _install_error_handlers(xlib: ctypes.CDLL, display: int) -> bool:
    """Install process-wide protocol/IO handlers and the per-display exit handler.

    Returns True if the connection can survive an IO error (i.e. the running
    libX11 provides XSetIOErrorExitHandler); False on very old libX11, where
    a lost connection still exits the process (the pre-handler behavior).
    Both pieces are required for survival: the IO error handler stops the
    default handler's own exit(1), and the exit handler replaces the final
    unconditional exit inside _XIOError.
    """
    global _error_handler_ref, _io_error_handler_ref, _io_exit_handler_ref

    if _error_handler_ref is None:
        xlib.XSetErrorHandler.restype = ctypes.c_void_p
        xlib.XSetErrorHandler.argtypes = [_X_ERROR_HANDLER_TYPE]
        xlib.XSetErrorHandler(_on_x_protocol_error)
        _error_handler_ref = _on_x_protocol_error

    try:
        exit_setter = xlib.XSetIOErrorExitHandler
    except AttributeError:
        # Without the exit-handler API a returning IO handler still ends in
        # exit(1), so leave the default in place (it at least prints a reason).
        return False

    if _io_error_handler_ref is None:
        xlib.XSetIOErrorHandler.restype = ctypes.c_void_p
        xlib.XSetIOErrorHandler.argtypes = [_X_IO_ERROR_HANDLER_TYPE]
        xlib.XSetIOErrorHandler(_on_x_io_error)
        _io_error_handler_ref = _on_x_io_error

    exit_setter.restype = None
    exit_setter.argtypes = [ctypes.c_void_p, _X_IO_ERROR_EXIT_HANDLER_TYPE, ctypes.c_void_p]
    exit_setter(display, _on_x_io_error_exit, None)
    _io_exit_handler_ref = _on_x_io_error_exit
    return True


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

    xtst.XTestQueryExtension.restype = c_int
    xtst.XTestQueryExtension.argtypes = [
        c_void_p,  # display
        ptr(c_int),  # event_base_return
        ptr(c_int),  # error_base_return
        ptr(c_int),  # major_version_return
        ptr(c_int),  # minor_version_return
    ]

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

        # Without these, Xlib's default handlers exit() the whole process on a
        # protocol error or a dropped server connection - no exception, no
        # chance for the recorder to flush its partially written session file.
        self._survives_io_error = _install_error_handlers(self._xlib, self._display)

        event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        major = ctypes.c_int()
        minor = ctypes.c_int()
        if not self._xtst.XTestQueryExtension(
            self._display,
            ctypes.byref(event_base),
            ctypes.byref(error_base),
            ctypes.byref(major),
            ctypes.byref(minor),
        ):
            self._xlib.XCloseDisplay(self._display)
            self._display = 0
            raise RuntimeError(
                "The X server does not support the XTest extension, which CursorTrack "
                "requires for input emulation. Virtually all servers (including Xvfb "
                "and XWayland) ship it; check your server configuration."
            )

        self._screen = self._xlib.XDefaultScreen(self._display)
        self._root = self._xlib.XRootWindow(self._display, self._screen)
        self._listener: Any | None = None

    def __del__(self) -> None:
        display = getattr(self, "_display", None)
        if display and display not in _DEAD_DISPLAYS:
            with contextlib.suppress(Exception):
                self._xlib.XCloseDisplay(display)
            self._display = 0

    def _ensure_alive(self) -> None:
        if self._display in _DEAD_DISPLAYS:
            raise RuntimeError(
                "The X11 display connection has been lost (the X server closed or the "
                "session ended). Create a new backend instance to reconnect."
            )

    def read_position(self) -> tuple[int, int]:
        self._ensure_alive()
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
        # The connection can die inside the query itself; the failing call
        # returns garbage rather than valid coordinates, so re-check.
        self._ensure_alive()
        return int(root_x.value), int(root_y.value)

    def _sync(self) -> None:
        # A full round-trip (not just XFlush) is required: flushing the request
        # buffer alone was observed to leave XTest fake events undelivered to
        # other clients' hooks, whereas XSync guarantees the server processed them.
        # pynput's own Linux controller syncs after every injection for the same reason.
        self._xlib.XSync(self._display, 0)
        self._ensure_alive()

    def set_position(self, x: int, y: int) -> None:
        self._ensure_alive()
        self._xlib.XWarpPointer(self._display, 0, self._root, 0, 0, 0, 0, int(x), int(y))
        self._sync()

    def get_screen_size(self) -> tuple[int, int]:
        self._ensure_alive()
        width = self._xlib.XDisplayWidth(self._display, self._screen)
        height = self._xlib.XDisplayHeight(self._display, self._screen)
        return int(width), int(height)

    def click(self, button: str, pressed: bool) -> None:
        self._ensure_alive()
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
        self._ensure_alive()
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

        try:
            verify_listener_running(
                self._listener,
                "The pynput mouse hook failed to start on this X11 display. "
                "Check that DISPLAY is set and the X server permits input "
                "hooks (XTest/XRecord); the process may lack the required "
                "permissions.",
            )
        except RuntimeError:
            self._listener = None
            raise

    def stop_listening(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            with contextlib.suppress(Exception):
                self._listener.join(timeout=2.0)
            self._listener = None
