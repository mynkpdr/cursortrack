"""Integration tests for the cursortrack Typer CLI interface."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Callable

from typer.testing import CliRunner

from cursortrack.backends import BACKEND_CLASSES
from cursortrack.backends.base import InputBackend
from cursortrack.cli.app import app
from cursortrack.core.events import ButtonEvent
from cursortrack.core.session import Session

runner = CliRunner()


class CornerAbortBackend(InputBackend):
    """Mock backend whose reported position becomes a screen corner after N reads.

    Used to deterministically exercise the playback fail-safe (which normally
    depends on a human physically moving the mouse) without a real display.
    """

    #: Number of read_position() calls to answer normally before reporting a corner.
    #: Set by each test before registering this class, since get_backend()
    #: instantiates backends with no constructor arguments.
    trigger_after = 0

    def __init__(self) -> None:
        self.pos = (500, 500)
        self.reads = 0

    def read_position(self) -> tuple[int, int]:
        self.reads += 1
        if self.reads > type(self).trigger_after:
            return (0, 0)
        return self.pos

    def set_position(self, x: int, y: int) -> None:
        self.pos = (x, y)

    def get_screen_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def click(self, button: str, pressed: bool) -> None:
        pass

    def scroll(self, sdx: int, sdy: int) -> None:
        pass

    def start_listening(
        self, on_event: Callable[[str, tuple[Any, ...], float], None], capture_mask: int
    ) -> None:
        pass

    def stop_listening(self) -> None:
        pass


class AheadOfTickClickBackend(InputBackend):
    """Mock backend that injects clicks stamped far ahead of the sampling tick.

    Emulates a recording loop that fell behind wall-clock time: the click
    timestamps round to frame numbers well past the loop's tick counter,
    which is the trigger for the historical frame-drift bug.
    """

    #: perf_counter offset applied to injected click timestamps (seconds).
    FUTURE_SECONDS = 20.0
    #: read_position() call numbers on which a click press+release is injected.
    INJECT_ON_READS = (5, 15)

    def __init__(self) -> None:
        self.reads = 0
        self.callback: Callable[[str, tuple[Any, ...], float], None] | None = None

    def read_position(self) -> tuple[int, int]:
        self.reads += 1
        if self.callback is not None and self.reads in self.INJECT_ON_READS:
            t_ahead = time.perf_counter() + self.FUTURE_SECONDS
            self.callback("click", (500, 500, "left", True), t_ahead)
            self.callback("click", (500, 500, "left", False), t_ahead)
        return (500, 500)

    def set_position(self, x: int, y: int) -> None:
        pass

    def get_screen_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def click(self, button: str, pressed: bool) -> None:
        pass

    def scroll(self, sdx: int, sdy: int) -> None:
        pass

    def start_listening(
        self, on_event: Callable[[str, tuple[Any, ...], float], None], _capture_mask: int
    ) -> None:
        self.callback = on_event

    def stop_listening(self) -> None:
        self.callback = None


class MixedButtonClickBackend(InputBackend):
    """Mock backend that injects one x2 click pair and one unsupported-button pair."""

    def __init__(self) -> None:
        self.reads = 0
        self.callback: Callable[[str, tuple[Any, ...], float], None] | None = None

    def read_position(self) -> tuple[int, int]:
        self.reads += 1
        if self.callback is not None and self.reads == 3:
            now = time.perf_counter()
            self.callback("click", (500, 500, "x2", True), now)
            self.callback("click", (500, 500, "x2", False), now)
            self.callback("click", (500, 500, "button10", True), now)
            self.callback("click", (500, 500, "button10", False), now)
        return (500, 500)

    def set_position(self, x: int, y: int) -> None:
        pass

    def get_screen_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def click(self, button: str, pressed: bool) -> None:
        pass

    def scroll(self, sdx: int, sdy: int) -> None:
        pass

    def start_listening(
        self, on_event: Callable[[str, tuple[Any, ...], float], None], _capture_mask: int
    ) -> None:
        self.callback = on_event

    def stop_listening(self) -> None:
        self.callback = None


def test_cli_version() -> None:
    """Verify printing package version is successful."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "cursortrack version" in result.stdout


def test_cli_doctor() -> None:
    """Verify running the doctor check environment command runs cleanly."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "CursorTrack System Diagnostics" in result.stdout


def test_cli_devices() -> None:
    """Verify active backend driver checks print target metrics."""
    result = runner.invoke(app, ["devices"])
    assert result.exit_code == 0
    assert "Input Backends & Devices" in result.stdout


def test_cli_record_and_info_and_export_and_play() -> None:
    """Run an end-to-end integration test of the recording lifecycle."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "test_session.ctrk")

        # 1. Record move and clicks for 1 second in mock backend (headless safe)
        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--capture",
                "move,click",
                "--hz",
                "50",
                "--seconds",
                "1",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert record_res.exit_code == 0
        assert os.path.exists(session_file)

        # 2. Run info command to verify header structure
        info_res = runner.invoke(app, ["info", session_file])
        assert info_res.exit_code == 0
        assert "Format Version" in info_res.stdout
        assert "Total Event Count" in info_res.stdout

        # 3. Export to CSV
        export_csv = os.path.join(tmpdir, "exported.csv")
        export_res_csv = runner.invoke(
            app, ["export", session_file, "--to", "csv", "-o", export_csv]
        )
        assert export_res_csv.exit_code == 0
        assert os.path.exists(export_csv)

        with open(export_csv, encoding="utf-8") as f:
            lines = f.readlines()
            assert "t,type,x,y,button,sdx,sdy,touch_id" in lines[0]
            assert len(lines) > 1

        # 4. Export to JSONL
        export_jsonl = os.path.join(tmpdir, "exported.jsonl")
        export_res_jsonl = runner.invoke(
            app, ["export", session_file, "--to", "jsonl", "-o", export_jsonl]
        )
        assert export_res_jsonl.exit_code == 0
        assert os.path.exists(export_jsonl)

        with open(export_jsonl, encoding="utf-8") as f:
            line_data = json.loads(f.readline())
            assert "t" in line_data
            assert "x" in line_data
            assert "y" in line_data

        # 5. Play back the session in mock backend
        play_res = runner.invoke(
            app,
            [
                "play",
                session_file,
                "--backend",
                "mock",
                "--speed",
                "10",  # speed it up
                "--delay",
                "0",  # no delay countdown
                "--no-spin",
                "-q",
            ],
        )
        assert play_res.exit_code == 0


def test_record_rejects_unknown_capture_flag() -> None:
    """An unrecognized --capture value should fail fast with a usage error, not a traceback."""
    result = runner.invoke(
        app,
        ["record", "--backend", "mock", "--capture", "bogus", "--seconds", "0.1", "-q"],
    )
    assert result.exit_code == 2
    assert "Unknown capture flag 'bogus'" in result.output


def test_record_rejects_out_of_range_hz() -> None:
    """A --hz value outside 1..65535 should produce a clean error, not raise internally."""
    result = runner.invoke(
        app,
        ["record", "--backend", "mock", "--hz", "0", "--seconds", "0.1", "-q"],
    )
    assert result.exit_code == 1
    assert "sample rate must be 1..65535" in result.output


def test_record_default_level_is_valid_for_zlib_fallback() -> None:
    """--level must default sensibly per resolved codec.

    Regression test: the old flat default of 19 (calibrated for zstd's 1-22 range) was
    passed straight into zlib.compressobj(), which only accepts 0-9, so plain
    `cursortrack record` crashed with a raw ValueError whenever zstandard wasn't
    installed - i.e. for anyone using the default (non-[zstd]) install.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--codec",
                "zlib",
                "--seconds",
                "0.1",
                "--no-spin",
                "-q",
            ],
        )
        assert result.exit_code == 0
        assert os.path.exists(session_file)


def test_record_rejects_out_of_range_level_for_resolved_codec() -> None:
    """An explicit --level outside the resolved codec's valid range should error cleanly."""
    result = runner.invoke(
        app,
        [
            "record",
            "--backend",
            "mock",
            "--codec",
            "zlib",
            "--level",
            "19",
            "--seconds",
            "0.1",
            "-q",
        ],
    )
    assert result.exit_code == 1
    assert "--level must be 0..9 for the zlib codec" in result.output


def test_record_completion_summary_states_actual_codec() -> None:
    """The completion summary must name the codec actually used, not leave it implicit.

    Relevant because --codec auto silently picks zlib vs zstd depending on what's
    installed; the user should be able to see which one was used without a separate
    `cursortrack info` call.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--codec",
                "zlib",
                "--seconds",
                "0.1",
                "--no-spin",
                "-q",
            ],
        )
        assert result.exit_code == 0
        assert "Codec:       zlib" in result.output


def test_record_duration_limit_matches_hz_and_seconds() -> None:
    """The recorded frame count should match round(hz * seconds), the documented contract."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "duration.ctrk")
        hz = 20
        seconds = 0.5

        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--capture",
                "move",
                "--hz",
                str(hz),
                "--seconds",
                str(seconds),
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert result.exit_code == 0

        session = Session.load(session_file)
        assert session.rate == hz
        # +1 accounts for the synthetic frame-0 event every recording starts with.
        assert len(session.events) == round(hz * seconds) + 1


def test_record_frame_clock_does_not_compound_drift_on_ahead_events() -> None:
    """Click timestamps rounding past the move tick must not stretch the timeline.

    Regression test: the recorder stamped moves with its tick counter but
    clicks with wall-clock frames. When a click's frame landed ahead of the
    tick, the next move rewound the bookkeeping below what had actually been
    encoded, so every later ahead-of-tick event re-encoded the same wall-clock
    gap on top of the decoder's already-advanced frame counter. Two clicks
    injected 20s ahead thus pushed the final frame to ~2x the offset instead
    of ~1x, permanently stretching playback timing.
    """
    hz = 50
    future_frames = round(AheadOfTickClickBackend.FUTURE_SECONDS * hz)  # 1000

    BACKEND_CLASSES["mock"] = AheadOfTickClickBackend
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "drift.ctrk")
        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--capture",
                "move,click",
                "--hz",
                str(hz),
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
        assert result.exit_code == 0

        session = Session.load(session_file)
        clicks = [e for e in session.events if isinstance(e, ButtonEvent)]
        assert len(clicks) == 4  # two injected press+release pairs

        final_frame = session.events[-1].frame
        # Sanity: the injected clicks really did land on the future timeline.
        assert final_frame >= future_frames
        # With consistent bookkeeping the timeline absorbs the 20s offset once
        # (~1000 frames + the recorded ticks). The old bug re-added it per
        # click burst, landing near 2x. Generous margin for CI loop jitter.
        assert final_frame < round(1.5 * future_frames)


def test_record_preserves_side_buttons_and_drops_unknown_ones() -> None:
    """x1/x2 must be stored under their own ids; unsupported buttons must be dropped.

    Regression test: any button name outside the format's vocabulary was mapped
    to id 0 - i.e. recorded as a *left* click - so replay performed left clicks
    the user never made. On Linux this bit every side-button press (pynput
    reports them as "button8"/"button9" before backend normalization).
    """
    BACKEND_CLASSES["mock"] = MixedButtonClickBackend
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "buttons.ctrk")
        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--capture",
                "move,click",
                "--hz",
                "50",
                "--seconds",
                "0.3",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
                "-d",
                "0",
            ],
        )
        assert result.exit_code == 0
        assert "unsupported button 'button10'" in result.output

        clicks = [e for e in Session.load(session_file).events if isinstance(e, ButtonEvent)]
        assert [(c.button, c.pressed) for c in clicks] == [("x2", True), ("x2", False)]


def test_play_failsafe_aborts_immediately_on_corner() -> None:
    """If the physical cursor is already in a corner, playback should abort on the first tick."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--hz",
                "20",
                "--seconds",
                "0.5",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert record_res.exit_code == 0

        CornerAbortBackend.trigger_after = 0
        BACKEND_CLASSES["mock"] = CornerAbortBackend

        play_res = runner.invoke(
            app,
            [
                "play",
                session_file,
                "--backend",
                "mock",
                "--speed",
                "50",
                "--delay",
                "0",
                "--no-spin",
            ],
        )
        assert play_res.exit_code == 0
        assert "Fail-safe triggered" in play_res.output
        # An aborted playback must not claim success.
        assert "Playback complete" not in play_res.output


def test_play_prints_completion_message_only_on_success() -> None:
    """A playback that runs to completion should print the success message; loud mode."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--hz",
                "20",
                "--seconds",
                "0.2",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert record_res.exit_code == 0

        play_res = runner.invoke(
            app,
            [
                "play",
                session_file,
                "--backend",
                "mock",
                "--speed",
                "50",
                "--delay",
                "0",
                "--no-spin",
            ],
        )
        assert play_res.exit_code == 0
        assert "Playback complete" in play_res.output


def test_record_refuses_to_overwrite_existing_file_without_force() -> None:
    """Recording to a path that already exists should fail fast, not clobber it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        with open(session_file, "wb") as f:
            f.write(b"pre-existing contents")

        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--seconds",
                "0.1",
                "--no-spin",
                "-q",
            ],
        )
        assert result.exit_code == 1
        assert "Refusing to overwrite" in result.output

        with open(session_file, "rb") as f:
            assert f.read() == b"pre-existing contents"


def test_record_overwrites_existing_file_with_force() -> None:
    """--force must allow recording to replace a pre-existing file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        with open(session_file, "wb") as f:
            f.write(b"pre-existing contents")

        result = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--seconds",
                "0.1",
                "--no-spin",
                "-q",
                "--force",
            ],
        )
        assert result.exit_code == 0
        assert os.path.getsize(session_file) > len(b"pre-existing contents")


def test_export_refuses_to_overwrite_existing_destination_without_force() -> None:
    """Exporting to a path that already exists should fail fast, not clobber it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--seconds",
                "0.1",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert record_res.exit_code == 0

        export_csv = os.path.join(tmpdir, "exported.csv")
        with open(export_csv, "w", encoding="utf-8") as f:
            f.write("pre-existing contents")

        result = runner.invoke(app, ["export", session_file, "--to", "csv", "-o", export_csv])
        assert result.exit_code == 1
        assert "Refusing to overwrite" in result.output

        with open(export_csv, encoding="utf-8") as f:
            assert f.read() == "pre-existing contents"

        force_result = runner.invoke(
            app, ["export", session_file, "--to", "csv", "-o", export_csv, "--force"]
        )
        assert force_result.exit_code == 0
        with open(export_csv, encoding="utf-8") as f:
            assert "pre-existing contents" not in f.read()


def test_export_refuses_same_path_as_input() -> None:
    """Exporting a jsonl file onto itself must be refused, even with --force."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--seconds",
                "0.1",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert record_res.exit_code == 0

        jsonl_file = os.path.join(tmpdir, "session.jsonl")
        export_res = runner.invoke(app, ["export", session_file, "--to", "jsonl", "-o", jsonl_file])
        assert export_res.exit_code == 0
        with open(jsonl_file, encoding="utf-8") as f:
            original_contents = f.read()

        result = runner.invoke(
            app, ["export", jsonl_file, "--to", "jsonl", "-o", jsonl_file, "--force"]
        )
        assert result.exit_code == 1
        assert "same file as the input" in result.output

        with open(jsonl_file, encoding="utf-8") as f:
            assert f.read() == original_contents


def test_play_loop_runs_multiple_passes_before_failsafe_stops_it() -> None:
    """--loop should replay the session again after finishing, not just play it once."""
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = os.path.join(tmpdir, "session.ctrk")
        record_res = runner.invoke(
            app,
            [
                "record",
                "-o",
                session_file,
                "--backend",
                "mock",
                "--hz",
                "20",
                "--seconds",
                "0.2",
                "--codec",
                "raw",
                "--no-spin",
                "-q",
            ],
        )
        assert record_res.exit_code == 0

        events_per_pass = len(Session.load(session_file).events)
        # Let one full pass complete normally, then force a corner abort early in the second pass.
        CornerAbortBackend.trigger_after = events_per_pass + 1
        BACKEND_CLASSES["mock"] = CornerAbortBackend

        play_res = runner.invoke(
            app,
            [
                "play",
                session_file,
                "--backend",
                "mock",
                "--speed",
                "50",
                "--delay",
                "0",
                "--loop",
                "--no-spin",
            ],
        )
        assert play_res.exit_code == 0
        assert "Replaying loop..." in play_res.output
        assert "Fail-safe triggered" in play_res.output
        assert "Playback complete" not in play_res.output
