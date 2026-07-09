"""Tests for the macOS CoreGraphics backend.

Real backend tests only run on `sys.platform == "darwin"`. A further subset
additionally requires Accessibility permission (`AXIsProcessTrusted()`):
GitHub's macOS runners do not grant it, so those tests skip themselves rather
than fail (see .github/workflows/ci.yml). The mock-based tests at the bottom
exercise pure Python logic (button-name mapping, no-op behavior) against a
fake CoreGraphics/CoreFoundation, so they run on every platform, including
this development machine.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Any

import pytest
from typer.testing import CliRunner

from cursortrack.backends import get_backend, resolve_backend_name
from cursortrack.cli.app import app

IS_DARWIN = sys.platform == "darwin"

requires_darwin = pytest.mark.skipif(
    not IS_DARWIN, reason="Requires macOS to exercise the real CoreGraphics backend."
)

runner = CliRunner()


def _ax_is_process_trusted() -> bool:
    """Probe AXIsProcessTrusted() directly, without constructing a backend."""
    import ctypes

    aps = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    aps.AXIsProcessTrusted.restype = ctypes.c_bool
    aps.AXIsProcessTrusted.argtypes = []
    return bool(aps.AXIsProcessTrusted())


requires_accessibility = pytest.mark.skipif(
    not (IS_DARWIN and _ax_is_process_trusted()),
    reason=(
        "Requires macOS with Accessibility permission granted. GitHub's macOS "
        "runners cannot grant this, so CGEventPost-based emulation and pynput "
        "hooks are untestable there - see docs/architecture.md."
    ),
)


@pytest.mark.skipif(not IS_DARWIN, reason="Auto-resolution check only meaningful on macOS.")
def test_auto_backend_resolves_to_macos() -> None:
    """On a macOS host, 'auto' must select the macos backend key."""
    assert resolve_backend_name("auto") == "macos"


# --- Real backend tests: no Accessibility permission required ---------------


@requires_darwin
def test_backend_initializes_and_loads_frameworks() -> None:
    backend = get_backend("macos")
    assert backend._cg is not None
    assert backend._cf is not None


@requires_darwin
def test_screen_size_is_positive() -> None:
    backend = get_backend("macos")
    width, height = backend.get_screen_size()
    assert width > 0
    assert height > 0


@requires_darwin
def test_read_position_returns_plausible_ints() -> None:
    backend = get_backend("macos")
    width, height = backend.get_screen_size()
    x, y = backend.read_position()
    assert isinstance(x, int)
    assert isinstance(y, int)
    # A generous bound, not a tight one: multi-display setups can report
    # positions outside the *main* display's bounds (get_screen_size()'s
    # known limitation - see docs/architecture.md), so only rule out wildly
    # implausible values rather than strictly bounding to (width, height).
    assert -width * 4 <= x <= width * 4
    assert -height * 4 <= y <= height * 4


@requires_darwin
def test_unknown_button_click_does_not_raise() -> None:
    """Even without Accessibility permission, an unknown button must be a safe no-op."""
    backend = get_backend("macos")
    backend.click("nonexistent-button", True)
    backend.click("nonexistent-button", False)


# --- Real backend tests: require Accessibility permission -------------------


@requires_accessibility
def test_set_and_read_position_round_trip() -> None:
    backend = get_backend("macos")
    backend.set_position(123, 217)
    assert backend.read_position() == (123, 217)
    backend.set_position(300, 40)
    assert backend.read_position() == (300, 40)


@requires_accessibility
def test_injected_clicks_and_scrolls_are_captured_by_hooks() -> None:
    """CGEventPost-injected events must be observable by the global capture listener."""
    pytest.importorskip("pynput")
    from cursortrack.core.events import CAP_CLICK, CAP_SCROLL

    captured: list[tuple[str, tuple[object, ...]]] = []

    def on_event(kind: str, payload: tuple[object, ...], _t: float) -> None:
        captured.append((kind, payload))

    recorder = get_backend("macos")
    recorder.start_listening(on_event, CAP_CLICK | CAP_SCROLL)
    try:
        time.sleep(0.5)  # let the listener thread attach its event tap

        player = get_backend("macos")
        player.set_position(200, 200)
        player.click("left", True)
        player.click("left", False)
        player.scroll(0, 1)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            clicks = [p for k, p in captured if k == "click"]
            scrolls = [p for k, p in captured if k == "scroll"]
            if len(clicks) >= 2 and len(scrolls) >= 1:
                break
            time.sleep(0.1)
    finally:
        recorder.stop_listening()

    clicks = [p for k, p in captured if k == "click"]
    scrolls = [p for k, p in captured if k == "scroll"]
    assert len(clicks) >= 2, f"expected a press and release, captured: {captured}"
    assert clicks[0][2] == "left" and clicks[0][3] is True
    assert clicks[1][2] == "left" and clicks[1][3] is False
    assert len(scrolls) >= 1, f"expected a scroll event, captured: {captured}"


@requires_accessibility
def test_cli_record_and_play_on_real_macos_backend() -> None:
    """End-to-end CLI lifecycle on the real macOS backend (not the mock)."""
    get_backend("macos").set_position(400, 400)

    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "macos_session.ctrk")

        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "macos",
                "--capture",
                "move",
                "--hz",
                "50",
                "--seconds",
                "0.5",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
                "-d",
                "0",
            ],
        )
        assert record_res.exit_code == 0
        assert os.path.exists(session_file)

        play_res = runner.invoke(
            app,
            [
                "play",
                session_file,
                "--backend",
                "macos",
                "--speed",
                "10",
                "--delay",
                "0",
                "--no-spin",
                "-q",
            ],
        )
        assert play_res.exit_code == 0


# --- Portable mock tests: run on every platform ------------------------------
#
# These construct a MacOSBackend instance without calling __init__ (which
# platform-guards and loads real frameworks), substituting fake CoreGraphics/
# CoreFoundation objects that record calls instead of touching any OS API.
# This lets the pure-Python button-mapping and no-op logic be verified from
# this Linux development machine, not just in the macOS CI job.


class _FakeCG:
    """Records every CoreGraphics call the backend makes, in call order.

    Method names intentionally match the real CDLL attribute names
    (CGEventCreate, etc.) rather than snake_case, since MacOSBackend calls
    them as `self._cg.CGEventCreate(...)`.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def CGEventCreate(self, source: object) -> int:  # noqa: N802
        self.calls.append(("CGEventCreate", (source,)))
        return 1

    def CGEventGetLocation(self, event: object) -> Any:  # noqa: N802
        from cursortrack.backends.macos import CGPoint

        self.calls.append(("CGEventGetLocation", (event,)))
        return CGPoint(11.0, 22.0)

    def CGEventCreateMouseEvent(self, *args: object) -> int:  # noqa: N802
        self.calls.append(("CGEventCreateMouseEvent", args))
        return 2

    def CGEventCreateScrollWheelEvent(self, *args: object) -> int:  # noqa: N802
        self.calls.append(("CGEventCreateScrollWheelEvent", args))
        return 3

    def CGEventPost(self, *args: object) -> None:  # noqa: N802
        self.calls.append(("CGEventPost", args))


class _FakeCF:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def CFRelease(self, event: object) -> None:  # noqa: N802
        self.calls.append(("CFRelease", (event,)))


def _mock_backend() -> tuple[object, _FakeCG, _FakeCF]:
    from cursortrack.backends.macos import MacOSBackend

    backend = object.__new__(MacOSBackend)
    fake_cg = _FakeCG()
    fake_cf = _FakeCF()
    backend._cg = fake_cg  # type: ignore[attr-defined]
    backend._cf = fake_cf  # type: ignore[attr-defined]
    backend._listener = None  # type: ignore[attr-defined]
    return backend, fake_cg, fake_cf


def test_unknown_button_is_a_noop_via_mock() -> None:
    """Regression test: unknown button names must never fall back to a real click.

    Mirrors test_linux_backend.py's equivalent - substituting a left click for
    an unrecognized name would perform a real, potentially destructive action
    the user never recorded.
    """
    backend, fake_cg, fake_cf = _mock_backend()

    backend.click("nonexistent-button", True)
    backend.click("nonexistent-button", False)

    assert fake_cg.calls == []
    assert fake_cf.calls == []


@pytest.mark.parametrize(
    ("button", "pressed", "expected_type_const", "expected_button_const"),
    [
        ("left", True, "K_CG_EVENT_LEFT_MOUSE_DOWN", "K_CG_MOUSE_BUTTON_LEFT"),
        ("left", False, "K_CG_EVENT_LEFT_MOUSE_UP", "K_CG_MOUSE_BUTTON_LEFT"),
        ("right", True, "K_CG_EVENT_RIGHT_MOUSE_DOWN", "K_CG_MOUSE_BUTTON_RIGHT"),
        ("right", False, "K_CG_EVENT_RIGHT_MOUSE_UP", "K_CG_MOUSE_BUTTON_RIGHT"),
        ("middle", True, "K_CG_EVENT_OTHER_MOUSE_DOWN", "K_CG_MOUSE_BUTTON_CENTER"),
        ("middle", False, "K_CG_EVENT_OTHER_MOUSE_UP", "K_CG_MOUSE_BUTTON_CENTER"),
        ("x1", True, "K_CG_EVENT_OTHER_MOUSE_DOWN", "K_CG_MOUSE_BUTTON_X1"),
        ("x1", False, "K_CG_EVENT_OTHER_MOUSE_UP", "K_CG_MOUSE_BUTTON_X1"),
        ("x2", True, "K_CG_EVENT_OTHER_MOUSE_DOWN", "K_CG_MOUSE_BUTTON_X2"),
        ("x2", False, "K_CG_EVENT_OTHER_MOUSE_UP", "K_CG_MOUSE_BUTTON_X2"),
    ],
)
def test_click_maps_canonical_buttons_to_expected_cg_constants(
    button: str, pressed: bool, expected_type_const: str, expected_button_const: str
) -> None:
    """Every canonical button name (BUTTON_ID vocabulary) must map to the right
    CGEventType and CGMouseButton, matching the requirements table for
    left/right/middle/x1/x2 in cursortrack/backends/macos.py.
    """
    import cursortrack.backends.macos as macos_mod

    backend, fake_cg, _fake_cf = _mock_backend()

    backend.click(button, pressed)

    create_calls = [c for c in fake_cg.calls if c[0] == "CGEventCreateMouseEvent"]
    assert len(create_calls) == 1
    _name, args = create_calls[0]
    _source, event_type, _point, mouse_button = args
    assert event_type == getattr(macos_mod, expected_type_const)
    assert mouse_button == getattr(macos_mod, expected_button_const)


def test_scroll_passes_vertical_then_horizontal_to_scroll_wheel_event() -> None:
    """wheel1/wheel2 must be (sdy, sdx) - vertical then horizontal, not x/y order.

    Regression guard for a natural mix-up: CGEventCreateScrollWheelEvent's
    wheel1 is the *vertical* axis and wheel2 is *horizontal*.
    """
    backend, fake_cg, _fake_cf = _mock_backend()

    backend.scroll(3, 7)  # sdx=3, sdy=7

    scroll_calls = [c for c in fake_cg.calls if c[0] == "CGEventCreateScrollWheelEvent"]
    assert len(scroll_calls) == 1
    _name, args = scroll_calls[0]
    _source, _units, wheel_count, wheel1, wheel2 = args
    assert wheel_count == 2
    assert wheel1 == 7  # sdy (vertical)
    assert wheel2 == 3  # sdx (horizontal)


def test_pynput_button_alias_normalization_is_documented_passthrough() -> None:
    """PYNPUT_BUTTON_ALIASES mirrors linux.py's alias map, but is a documented
    no-op today: pynput's macOS Button enum only ever yields "left"/"right"/
    "middle" (see module docstring comment), so the lookup always falls
    through to the original name via `.get(name, name)`.
    """
    from cursortrack.backends.macos import PYNPUT_BUTTON_ALIASES

    for name in ("left", "right", "middle"):
        assert PYNPUT_BUTTON_ALIASES.get(name, name) == name


def test_read_position_rounds_and_releases_event_via_mock() -> None:
    backend, fake_cg, fake_cf = _mock_backend()

    x, y = backend.read_position()

    assert (x, y) == (11, 22)
    assert [c[0] for c in fake_cg.calls] == ["CGEventCreate", "CGEventGetLocation"]
    assert [c[0] for c in fake_cf.calls] == ["CFRelease"]


def test_set_position_posts_mouse_moved_via_mock() -> None:
    import cursortrack.backends.macos as macos_mod

    backend, fake_cg, fake_cf = _mock_backend()

    backend.set_position(50, 60)

    create_calls = [c for c in fake_cg.calls if c[0] == "CGEventCreateMouseEvent"]
    assert len(create_calls) == 1
    _source, event_type, point, _button = create_calls[0][1]
    assert event_type == macos_mod.K_CG_EVENT_MOUSE_MOVED
    assert (point.x, point.y) == (50.0, 60.0)
    assert [c[0] for c in fake_cg.calls if c[0] == "CGEventPost"] == ["CGEventPost"]
    assert len(fake_cf.calls) == 1
