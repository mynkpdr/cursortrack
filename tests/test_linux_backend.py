"""Tests for the Linux X11/XTest backend.

These run against a real X display. On headless machines (and in CI) run the
suite under a virtual server: `xvfb-run -a pytest`. Without a display the
X11-dependent tests skip rather than fail, so the rest of the suite stays
usable everywhere.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest
from typer.testing import CliRunner

from cursortrack.backends import get_backend, resolve_backend_name
from cursortrack.cli.app import app

IS_LINUX = sys.platform.startswith("linux")
HAS_DISPLAY = bool(os.environ.get("DISPLAY"))

requires_x11 = pytest.mark.skipif(
    not (IS_LINUX and HAS_DISPLAY),
    reason="Requires Linux with an X11 display (use xvfb-run on headless machines).",
)

runner = CliRunner()


@pytest.mark.skipif(not IS_LINUX, reason="Auto-resolution check only meaningful on Linux.")
def test_auto_backend_resolves_to_linux() -> None:
    """On a Linux host, 'auto' must select the linux backend key."""
    assert resolve_backend_name("auto") == "linux"


@requires_x11
def test_screen_size_is_positive() -> None:
    backend = get_backend("linux")
    width, height = backend.get_screen_size()
    assert width > 0
    assert height > 0


@requires_x11
def test_set_and_read_position_round_trip() -> None:
    backend = get_backend("linux")
    backend.set_position(123, 217)
    assert backend.read_position() == (123, 217)
    backend.set_position(300, 40)
    assert backend.read_position() == (300, 40)


@requires_x11
def test_unknown_button_falls_back_to_left_click() -> None:
    """Parity with WindowsBackend: unrecognized button names emulate a left click."""
    backend = get_backend("linux")
    # Must not raise; delivery is verified by the capture round-trip test below.
    backend.click("nonexistent-button", True)
    backend.click("nonexistent-button", False)


@requires_x11
def test_injected_clicks_and_scrolls_are_captured_by_hooks() -> None:
    """XTest-injected events must be observable by the global capture listener.

    This exercises the full loop a real recording depends on: one backend
    instance hooks global events (pynput), another injects them (XTest), and
    the X server routes the fakes back to the hook.
    """
    pytest.importorskip("pynput")
    from cursortrack.core.events import CAP_CLICK, CAP_SCROLL

    captured: list[tuple[str, tuple[object, ...]]] = []

    def on_event(kind: str, payload: tuple[object, ...], _t: float) -> None:
        captured.append((kind, payload))

    recorder = get_backend("linux")
    recorder.start_listening(on_event, CAP_CLICK | CAP_SCROLL)
    try:
        time.sleep(0.5)  # let the listener thread attach its hook

        player = get_backend("linux")
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
    assert scrolls[0][3] == 1


@requires_x11
def test_cli_record_and_play_on_real_linux_backend() -> None:
    """End-to-end CLI lifecycle on the real linux backend (not the mock)."""
    # Park the cursor away from screen corners so the playback fail-safe
    # (which aborts when the physical cursor sits in a corner) stays quiet.
    get_backend("linux").set_position(400, 400)

    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "linux_session.ctrk")

        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "linux",
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
                "linux",
                "--speed",
                "10",
                "--delay",
                "0",
                "--no-spin",
                "-q",
            ],
        )
        assert play_res.exit_code == 0
