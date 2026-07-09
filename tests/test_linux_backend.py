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
def test_unknown_button_is_a_noop() -> None:
    """Unrecognized button names must do nothing.

    Regression test: the old fallback substituted a *left* click, silently
    performing a real (potentially destructive) action the user never
    recorded. Delivery of valid buttons is verified by the round-trip tests.
    """
    pytest.importorskip("pynput")
    from cursortrack.core.events import CAP_CLICK

    captured: list[tuple[str, tuple[object, ...]]] = []

    def on_event(kind: str, payload: tuple[object, ...], _t: float) -> None:
        captured.append((kind, payload))

    recorder = get_backend("linux")
    recorder.start_listening(on_event, CAP_CLICK)
    try:
        time.sleep(0.5)  # let the listener thread attach its hook
        player = get_backend("linux")
        player.click("nonexistent-button", True)
        player.click("nonexistent-button", False)
        time.sleep(1.0)  # window in which a wrongly-substituted click would arrive
    finally:
        recorder.stop_listening()

    assert captured == [], f"unknown button emitted real events: {captured}"


@requires_x11
def test_side_buttons_round_trip_with_canonical_names() -> None:
    """XTest-injected x1/x2 clicks must be captured under their canonical names.

    Regression test: pynput's X11 listener reports side buttons as
    "button8"/"button9", which the recorder used to store as *left* clicks.
    The backend now normalizes them to the format's "x1"/"x2" vocabulary.
    """
    pytest.importorskip("pynput")
    from cursortrack.core.events import CAP_CLICK

    captured: list[tuple[str, tuple[object, ...]]] = []

    def on_event(kind: str, payload: tuple[object, ...], _t: float) -> None:
        captured.append((kind, payload))

    recorder = get_backend("linux")
    recorder.start_listening(on_event, CAP_CLICK)
    try:
        time.sleep(0.5)  # let the listener thread attach its hook

        player = get_backend("linux")
        for name in ("x1", "x2"):
            player.click(name, True)
            player.click(name, False)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len([p for k, p in captured if k == "click"]) >= 4:
                break
            time.sleep(0.1)
    finally:
        recorder.stop_listening()

    names = [p[2] for k, p in captured if k == "click"]
    assert names == ["x1", "x1", "x2", "x2"], f"captured button names: {names}"


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
def test_missing_xtest_or_dead_connection_guard_attributes() -> None:
    """The backend must probe XTest and know whether it can survive connection loss."""
    backend = get_backend("linux")
    # Construction succeeded, so the XTest probe passed on this server (Xvfb
    # and all real servers ship it). The survival flag must be a bool either way.
    assert isinstance(backend._survives_io_error, bool)


@requires_x11
def test_lost_x_connection_raises_instead_of_exiting_process() -> None:
    """A dying X server must surface as a Python exception, not a process exit.

    Regression test: without custom Xlib error handlers, libX11's defaults call
    exit() when the connection drops (session logout, SSH forwarding gone,
    Xvfb killed), terminating Python before the recorder can finalize its
    partially written session file. This spawns a disposable Xvfb, connects a
    backend to it, kills the server, and verifies the failure is catchable.
    """
    import shutil
    import subprocess

    from cursortrack.backends.linux import LinuxBackend

    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        pytest.skip("Xvfb binary not available to spawn a disposable X server.")

    # Allocate a display by probing candidates, using a successful backend
    # connection as the readiness signal. (Socket-file checks and -displayfd
    # are unreliable where /tmp/.X11-unix isn't writable, e.g. WSL - Xvfb
    # then serves only the abstract socket and never creates the file.)
    proc = None
    backend = None
    old_display = os.environ["DISPLAY"]
    for candidate in (91, 92, 93):
        attempt = subprocess.Popen(
            [xvfb, f":{candidate}", "-screen", "0", "320x240x24"],
            stderr=subprocess.DEVNULL,
        )
        os.environ["DISPLAY"] = f":{candidate}"
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and attempt.poll() is None:
                try:
                    backend = LinuxBackend()
                    break
                except RuntimeError:
                    time.sleep(0.1)
        finally:
            os.environ["DISPLAY"] = old_display
        # Only accept the connection if it is to *our* live server (a dead
        # `attempt` here means the display number was already taken).
        if backend is not None and attempt.poll() is None:
            proc = attempt
            break
        backend = None
        attempt.kill()
        attempt.wait(timeout=10)
    if proc is None or backend is None:
        pytest.skip("Could not start a disposable Xvfb on any candidate display.")

    try:
        if not backend._survives_io_error:
            pytest.skip("libX11 lacks XSetIOErrorExitHandler; connection loss still exits.")

        backend.read_position()  # sanity: the connection works while alive

        proc.terminate()
        proc.wait(timeout=10)

        with pytest.raises(RuntimeError, match="connection has been lost"):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                backend.read_position()
                time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


@requires_x11
def test_listener_is_running_after_start_listening() -> None:
    """A successfully started listener must report `running` and stop cleanly.

    Regression test for #14: start_listening() used to return without ever
    checking whether pynput's hook thread actually came up, so a failed hook
    install would record silently instead of raising.

    stop_listening() itself must tolerate pynput's teardown quirks (e.g. a
    Xorg listener raising AttributeError from stop() on a teardown race) -
    detecting a failed *start* is the point of #14, stop must never raise.
    """
    pytest.importorskip("pynput")
    from cursortrack.core.events import CAP_CLICK

    backend = get_backend("linux")
    backend.start_listening(lambda *_: None, CAP_CLICK)
    try:
        assert backend._listener is not None
        assert backend._listener.running
    finally:
        backend.stop_listening()

    assert backend._listener is None


@requires_x11
def test_stop_listening_is_best_effort_against_a_broken_listener() -> None:
    """stop_listening() must swallow errors from a listener's stop()/join().

    Regression test: pynput's X11 listener can raise `AttributeError:
    'Listener' object has no attribute '_display_record'` out of stop() on a
    teardown race (observed in CI on Python 3.14). stop_listening() is not
    the mechanism for detecting a failed start (that's verify_listener_running
    above), so it must never propagate a teardown-time exception.
    """
    backend = get_backend("linux")

    class BrokenListener:
        def stop(self) -> None:
            raise AttributeError("'Listener' object has no attribute '_display_record'")

        def join(self, timeout: float | None = None) -> None:
            del timeout
            raise RuntimeError("cannot join a listener that never started cleanly")

    backend._listener = BrokenListener()
    backend.stop_listening()

    assert backend._listener is None


def test_verify_listener_running_raises_for_a_dead_listener() -> None:
    """A listener whose `running` never flips True must raise, not be trusted.

    Regression test for #14: pynput surfaces hook-install failures (no
    display, hook rejected by the OS, missing permissions) by leaving the
    listener thread dead rather than raising from `start()`. This is a pure
    unit test against the shared verification helper (no real listener or
    display needed), so it runs on every platform.
    """
    from cursortrack.backends._pynput_listener import verify_listener_running

    class DeadListener:
        running = False

    with pytest.raises(RuntimeError, match="custom failure message"):
        verify_listener_running(DeadListener(), "custom failure message", timeout=0.05)


def test_verify_listener_running_accepts_a_listener_that_comes_up_late() -> None:
    """The poll must not fail a listener that flips `running` shortly after start()."""
    import threading

    from cursortrack.backends._pynput_listener import verify_listener_running

    class SlowListener:
        running = False

    listener = SlowListener()
    threading.Timer(0.05, lambda: setattr(listener, "running", True)).start()

    verify_listener_running(listener, "should not raise", timeout=0.5)


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
