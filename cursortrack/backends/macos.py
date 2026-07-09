"""macOS input tracking and emulation backend using CoreGraphics (via ctypes) and pynput hooks."""

from __future__ import annotations

import contextlib
import ctypes
import sys
from typing import Any, Callable, cast

from cursortrack.backends.base import InputBackend
from cursortrack.core.events import CAP_CLICK, CAP_SCROLL, CAP_TOUCH

# CGEventType values (CoreGraphics/CGEventTypes.h)
K_CG_EVENT_LEFT_MOUSE_DOWN = 1
K_CG_EVENT_LEFT_MOUSE_UP = 2
K_CG_EVENT_RIGHT_MOUSE_DOWN = 3
K_CG_EVENT_RIGHT_MOUSE_UP = 4
K_CG_EVENT_MOUSE_MOVED = 5
K_CG_EVENT_OTHER_MOUSE_DOWN = 25
K_CG_EVENT_OTHER_MOUSE_UP = 26

# CGEventTapLocation: inject at the lowest point of the HID event pipeline -
# the same stage a physical mouse/trackpad reports through, so other apps
# (and pynput's own listener) see the fake event identically to a real one.
K_CG_HID_EVENT_TAP = 0

# CGMouseButton values for CGEventCreateMouseEvent's `mouseButton` parameter.
# For kCGEventOtherMouse*, this doubles as the literal button number (2 is
# conventionally "middle"; 3/4 are the side buttons this format canonicalizes
# as x1/x2).
K_CG_MOUSE_BUTTON_LEFT = 0
K_CG_MOUSE_BUTTON_RIGHT = 1
K_CG_MOUSE_BUTTON_CENTER = 2
K_CG_MOUSE_BUTTON_X1 = 3
K_CG_MOUSE_BUTTON_X2 = 4

# CGScrollEventUnit: whole "line" steps, matching what a physical wheel
# reports and how the Linux/Windows backends already count a scroll "step"
# (X11 button taps, WHEEL_DELTA multiples) rather than raw pixels.
K_CG_SCROLL_EVENT_UNIT_LINE = 1

BUTTON_EVENT_MAP: dict[str, tuple[int, int, int]] = {
    # name -> (down_type, up_type, mouse_button)
    "left": (K_CG_EVENT_LEFT_MOUSE_DOWN, K_CG_EVENT_LEFT_MOUSE_UP, K_CG_MOUSE_BUTTON_LEFT),
    "right": (K_CG_EVENT_RIGHT_MOUSE_DOWN, K_CG_EVENT_RIGHT_MOUSE_UP, K_CG_MOUSE_BUTTON_RIGHT),
    "middle": (K_CG_EVENT_OTHER_MOUSE_DOWN, K_CG_EVENT_OTHER_MOUSE_UP, K_CG_MOUSE_BUTTON_CENTER),
    "x1": (K_CG_EVENT_OTHER_MOUSE_DOWN, K_CG_EVENT_OTHER_MOUSE_UP, K_CG_MOUSE_BUTTON_X1),
    "x2": (K_CG_EVENT_OTHER_MOUSE_DOWN, K_CG_EVENT_OTHER_MOUSE_UP, K_CG_MOUSE_BUTTON_X2),
}

# pynput's macOS listener (pynput.mouse._darwin.Button) only ever enumerates
# Quartz's three *named* buttons - left/right/middle - because it matches on
# raw CGEventType (kCGEventOtherMouseDown/Up), which macOS does not vary by
# button number the way X11 assigns distinct button8/button9 codes. So unlike
# linux.py's button8/button9 -> x1/x2 aliasing, pynput never hands us an "x1"
# or "x2" name to translate: side-button presses arrive indistinguishable from
# a middle click. This map (and its use below) exists for forward
# compatibility should a future pynput release start disambiguating
# kCGMouseEventButtonNumber, and is a documented no-op today - see
# docs/architecture.md for the resulting capture limitation.
PYNPUT_BUTTON_ALIASES: dict[str, str] = {}


class CGPoint(ctypes.Structure):
    """Layout of CGPoint (CoreGraphics/CGGeometry.h): two CGFloat fields.

    CGFloat is a double on every architecture macOS ships in the 64-bit-only
    era (Intel and Apple Silicon). Getting this wrong corrupts every function
    that takes or returns a CGPoint by value, including CGEventGetLocation.
    """

    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _load_framework(path: str) -> ctypes.CDLL:
    """Load a system framework by its well-known absolute path.

    Frameworks are loaded directly (not via ctypes.util.find_library) because
    these paths have been stable across every shipping macOS release; a
    missing file means a broken/non-standard OS install, not a version quirk.
    """
    try:
        return ctypes.CDLL(path)
    except OSError:
        raise RuntimeError(
            f"Could not load the required macOS framework at '{path}'. "
            "CursorTrack's macOS backend requires a standard macOS installation."
        )


def _declare_prototypes(cg: ctypes.CDLL, cf: ctypes.CDLL, aps: ctypes.CDLL) -> None:
    """Declare ctypes argument/return types for every CoreGraphics/CF/AX call we make.

    Declaring restype is not optional decoration: several of these functions
    return pointers (CGEventRef, both consumed and produced as c_void_p), and
    without an explicit restype ctypes truncates the 64-bit pointer to a
    32-bit int, corrupting every subsequent call built on that value.
    """
    c_void_p, c_uint32, c_int32, c_size_t = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_size_t,
    )

    cg.CGEventCreate.restype = c_void_p
    cg.CGEventCreate.argtypes = [c_void_p]  # CGEventSourceRef source

    cg.CGEventGetLocation.restype = CGPoint  # returned by value, not by pointer
    cg.CGEventGetLocation.argtypes = [c_void_p]

    cg.CGEventCreateMouseEvent.restype = c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [
        c_void_p,  # CGEventSourceRef source
        c_uint32,  # CGEventType mouseType
        CGPoint,  # mouseCursorPosition (by value)
        c_uint32,  # CGMouseButton mouseButton
    ]

    cg.CGEventCreateScrollWheelEvent.restype = c_void_p
    cg.CGEventCreateScrollWheelEvent.argtypes = [
        c_void_p,  # CGEventSourceRef source
        c_uint32,  # CGScrollEventUnit units
        c_uint32,  # CGWheelCount wheelCount
        c_int32,  # wheel1 (vertical axis)
        c_int32,  # wheel2 (horizontal axis)
    ]

    cg.CGEventPost.restype = None
    cg.CGEventPost.argtypes = [c_uint32, c_void_p]  # CGEventTapLocation, CGEventRef

    cg.CGMainDisplayID.restype = c_uint32
    cg.CGMainDisplayID.argtypes = []

    cg.CGDisplayPixelsWide.restype = c_size_t
    cg.CGDisplayPixelsWide.argtypes = [c_uint32]

    cg.CGDisplayPixelsHigh.restype = c_size_t
    cg.CGDisplayPixelsHigh.argtypes = [c_uint32]

    cf.CFRelease.restype = None
    cf.CFRelease.argtypes = [c_void_p]

    # Boolean AXIsProcessTrusted(void) - Boolean is a 1-byte unsigned char in
    # every macOS SDK; c_bool matches its size and truthiness on all supported
    # architectures.
    aps.AXIsProcessTrusted.restype = ctypes.c_bool
    aps.AXIsProcessTrusted.argtypes = []


class MacOSBackend(InputBackend):
    """macOS implementation using CoreGraphics ctypes for emulation/reading, pynput for hooks.

    Requires the process (or the terminal/app bundle hosting it) to be granted
    Accessibility permission (System Settings -> Privacy & Security ->
    Accessibility). Without it, CGEventPost silently drops every injected
    event - no exception is raised - and pynput's mouse.Listener never fires.
    See docs/architecture.md for the full permission model and its CI
    implications (GitHub's macOS runners cannot grant this permission).
    """

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("MacOSBackend can only be initialized on macOS systems.")

        self._cg = _load_framework("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self._cf = _load_framework(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._aps = _load_framework(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        _declare_prototypes(self._cg, self._cf, self._aps)

        if not self._aps.AXIsProcessTrusted():
            # Not a hard failure: read_position()/get_screen_size() work fine
            # without this permission, and a user running `doctor` or `info`
            # shouldn't be blocked. Emulation and capture just won't do anything.
            sys.stderr.write(
                "cursortrack: Accessibility permission not granted to this process. "
                "Cursor emulation (set_position/click/scroll) will silently do nothing "
                "and click/scroll capture will not fire until you grant it under "
                "System Settings -> Privacy & Security -> Accessibility.\n"
            )

        self._listener: Any | None = None

    def _current_point(self) -> CGPoint:
        event = self._cg.CGEventCreate(None)
        # ctypes.CDLL attribute calls are typed Any regardless of the restype
        # assigned at runtime; cast back to the declared CGPoint so callers
        # get real attribute (.x/.y) checking.
        point = cast(CGPoint, self._cg.CGEventGetLocation(event))
        self._cf.CFRelease(event)
        return point

    def read_position(self) -> tuple[int, int]:
        point = self._current_point()
        return round(point.x), round(point.y)

    def set_position(self, x: int, y: int) -> None:
        # Post a move event rather than warping (CGWarpMouseCursorPosition) so
        # other applications observe the motion the same way they would a real
        # mouse move - matching XWarpPointer/SetCursorPos's visible-to-apps
        # semantics on Linux/Windows. Always kCGEventMouseMoved regardless of
        # button state: every backend treats click() as a separate call, and
        # play.py always sequences set_position() followed by click()/scroll()
        # rather than relying on drag semantics from the move itself.
        point = CGPoint(float(x), float(y))
        event = self._cg.CGEventCreateMouseEvent(
            None, K_CG_EVENT_MOUSE_MOVED, point, K_CG_MOUSE_BUTTON_LEFT
        )
        self._cg.CGEventPost(K_CG_HID_EVENT_TAP, event)
        self._cf.CFRelease(event)

    def get_screen_size(self) -> tuple[int, int]:
        # Main display only - a known limitation shared with the Windows
        # backend's pre-#16 GetSystemMetrics(SM_CXSCREEN)/(SM_CYSCREEN);
        # multi-monitor parity is tracked separately in docs/architecture.md.
        #
        # InputBackend.get_screen_bounds() is not overridden here: its
        # default (origin (0, 0), this method's width/height) is already
        # correct for the main display, since Quartz defines its global
        # coordinate space with (0, 0) at the main display's top-left by
        # construction - there is no separate "virtual desktop origin" query
        # to make, unlike Windows' SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN.
        display_id = self._cg.CGMainDisplayID()
        width = self._cg.CGDisplayPixelsWide(display_id)
        height = self._cg.CGDisplayPixelsHigh(display_id)
        return int(width), int(height)

    def click(self, button: str, pressed: bool) -> None:
        # Unknown button names are a no-op: substituting a left click (the old
        # fallback pattern this format's vocabulary was deliberately changed
        # to avoid) performs a real, potentially destructive action the user
        # never recorded.
        mapping = BUTTON_EVENT_MAP.get(button.lower())
        if mapping is None:
            return
        down_type, up_type, mouse_button = mapping
        event_type = down_type if pressed else up_type

        # CGEventCreateMouseEvent requires an explicit position; posting one
        # at (0, 0) would jump the cursor there. Use wherever the cursor
        # currently is, matching Linux/Windows: clicks act "where the cursor
        # is right now", and play.py always calls set_position() immediately
        # beforehand.
        point = self._current_point()
        event = self._cg.CGEventCreateMouseEvent(None, event_type, point, mouse_button)
        self._cg.CGEventPost(K_CG_HID_EVENT_TAP, event)
        self._cf.CFRelease(event)

    def scroll(self, sdx: int, sdy: int) -> None:
        # wheel1/wheel2 map to the vertical/horizontal axes respectively, not
        # x/y positional order - verified against pynput's own Darwin
        # controller, which passes (dy, dx) in that same slot order to
        # CGEventCreateScrollWheelEvent.
        event = self._cg.CGEventCreateScrollWheelEvent(
            None, K_CG_SCROLL_EVENT_UNIT_LINE, 2, int(sdy), int(sdx)
        )
        self._cg.CGEventPost(K_CG_HID_EVENT_TAP, event)
        self._cf.CFRelease(event)

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        # Dynamic import of pynput listener (uses a Quartz event tap on macOS)
        try:
            from pynput import mouse
        except ImportError:
            raise ImportError(
                "Capturing click, scroll, or touch events requires 'pynput'. "
                "Install it using 'pip install pynput' or 'pip install cursortrack[macos]'."
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
            # Best-effort: tearing down a Quartz event tap during interpreter
            # shutdown or after the process lost trust mid-session has been
            # observed to raise from pynput's own cleanup path.
            with contextlib.suppress(Exception):
                self._listener.stop()
            self._listener = None
