"""Tests for Windows Precision Touchpad diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import cursortrack.backends._windows_touchpad as touchpad_module
from cursortrack.backends._windows_touchpad import TouchpadProbe
from cursortrack.cli import doctor
from cursortrack.cli.app import app

runner = CliRunner()


class _DiagnosticListener:
    runtime_error: str | None = None
    emit_on_start = False
    stopped = False

    def __init__(self, on_scroll: Any) -> None:
        self._on_scroll = on_scroll

    def start(self) -> bool:
        if type(self).emit_on_start:
            self._on_scroll(0, -1, 1.0)
        return True

    def stop(self) -> None:
        type(self).stopped = True


def _probe() -> TouchpadProbe:
    return TouchpadProbe(
        device_count=1,
        compatible_device_count=1,
        reverse_direction=False,
    )


def test_touchpad_test_no_events_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _DiagnosticListener.emit_on_start = False
    _DiagnosticListener.stopped = False
    monkeypatch.setattr(
        touchpad_module,
        "PrecisionTouchpadScrollListener",
        _DiagnosticListener,
    )

    with pytest.raises(typer.Exit) as caught:
        doctor._run_touchpad_test(0, _probe())

    assert caught.value.exit_code == 1
    assert _DiagnosticListener.stopped


def test_touchpad_test_success_requires_a_reconstructed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _DiagnosticListener.emit_on_start = True
    _DiagnosticListener.stopped = False
    monkeypatch.setattr(
        touchpad_module,
        "PrecisionTouchpadScrollListener",
        _DiagnosticListener,
    )

    doctor._run_touchpad_test(0, _probe())

    assert _DiagnosticListener.stopped


def test_touchpad_test_startup_error_is_cleanly_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingListener(_DiagnosticListener):
        def start(self) -> bool:
            raise OSError("registration denied")

    monkeypatch.setattr(
        touchpad_module,
        "PrecisionTouchpadScrollListener",
        FailingListener,
    )

    with pytest.raises(typer.Exit) as caught:
        doctor._run_touchpad_test(0, _probe())

    assert caught.value.exit_code == 1


def test_touchpad_test_configuration_error_is_cleanly_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidConfigurationListener:
        def __init__(self, _on_scroll: Any) -> None:
            raise ValueError("invalid sensitivity")

    monkeypatch.setattr(
        touchpad_module,
        "PrecisionTouchpadScrollListener",
        InvalidConfigurationListener,
    )

    with pytest.raises(typer.Exit) as caught:
        doctor._run_touchpad_test(0, _probe())

    assert caught.value.exit_code == 1


def test_touchpad_test_interrupt_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    _DiagnosticListener.emit_on_start = False
    monkeypatch.setattr(
        touchpad_module,
        "PrecisionTouchpadScrollListener",
        _DiagnosticListener,
    )
    monkeypatch.setattr(doctor.time, "monotonic", lambda: 0.0)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(doctor.time, "sleep", interrupt)

    with pytest.raises(typer.Exit) as caught:
        doctor._run_touchpad_test(1, _probe())

    assert caught.value.exit_code == 130


def test_doctor_touchpad_test_does_not_print_generic_health_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor,
        "sys",
        SimpleNamespace(platform="win32", version="3.12.0 test"),
    )
    monkeypatch.setattr(touchpad_module, "probe_precision_touchpad", _probe)
    monkeypatch.setattr(doctor, "_run_touchpad_test", lambda _seconds, _probe_result: None)

    result = runner.invoke(app, ["doctor", "--touchpad-test", "1"])

    assert result.exit_code == 0
    assert "All features active" not in result.output


def test_doctor_rejects_invalid_touchpad_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor,
        "sys",
        SimpleNamespace(platform="win32", version="3.12.0 test"),
    )
    monkeypatch.setattr(touchpad_module, "probe_precision_touchpad", _probe)
    monkeypatch.setenv("CURSORTRACK_WINDOWS_TOUCHPAD", "sometimes")

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "CURSORTRACK_WINDOWS_TOUCHPAD" in result.output


@pytest.mark.parametrize("value", ["not-a-number", "nan", "0.001", "0.101"])
def test_doctor_rejects_invalid_touchpad_sensitivity(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setattr(
        doctor,
        "sys",
        SimpleNamespace(platform="win32", version="3.12.0 test"),
    )
    monkeypatch.setattr(touchpad_module, "probe_precision_touchpad", _probe)
    monkeypatch.setenv("CURSORTRACK_TOUCHPAD_STEP_FRACTION", value)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "CURSORTRACK_TOUCHPAD_STEP_FRACTION" in result.output


def test_touchpad_test_requires_a_positive_duration() -> None:
    result = runner.invoke(app, ["doctor", "--touchpad-test", "0"])

    assert result.exit_code == 2
