"""Tests for playback-only scroll direction and scaling controls."""

from __future__ import annotations

from typing import ClassVar

import pytest
from typer.testing import CliRunner

from cursortrack.backends import BACKEND_CLASSES
from cursortrack.cli.app import app
from cursortrack.cli.play import _ScrollTransform
from cursortrack.core.codec import CODEC_RAW
from cursortrack.core.events import CAP_MOVE, CAP_SCROLL, encode_scroll
from cursortrack.core.format import pack_header
from tests.conftest import MockBackend

runner = CliRunner()


class ScrollPlaybackBackend(MockBackend):
    injected_scrolls: ClassVar[list[tuple[int, int]]] = []

    def scroll(self, sdx: int, sdy: int) -> None:
        type(self).injected_scrolls.append((sdx, sdy))


def _write_scroll_session(path: str, steps: list[tuple[int, int]]) -> None:
    body = bytearray()
    for sdx, sdy in steps:
        encode_scroll(body, 1, sdx, sdy, 0, 0)
    header = pack_header(
        codec=CODEC_RAW,
        rate=100,
        scr_w=1920,
        scr_h=1080,
        start=1000.0,
        x0=500,
        y0=500,
        capture=CAP_MOVE | CAP_SCROLL,
    )
    with open(path, "wb") as output:
        output.write(header + body)


def test_scroll_transform_inverts_both_axes() -> None:
    transform = _ScrollTransform(scale=1.0, invert=True)

    assert transform.apply(2, -3) == (-2, 3)


def test_scroll_transform_accumulates_fractional_steps() -> None:
    transform = _ScrollTransform(scale=0.5, invert=False)

    assert transform.apply(0, -1) == (0, 0)
    assert transform.apply(0, -1) == (0, -1)
    assert transform.apply(1, 0) == (0, 0)
    assert transform.apply(1, 0) == (1, 0)


def test_play_applies_inversion_and_fractional_scale(tmp_path: object) -> None:
    session_file = str(tmp_path) + "/scroll.ctrk"
    _write_scroll_session(session_file, [(0, -1), (0, -1)])
    ScrollPlaybackBackend.injected_scrolls = []
    BACKEND_CLASSES["mock"] = ScrollPlaybackBackend

    result = runner.invoke(
        app,
        [
            "play",
            session_file,
            "--backend",
            "mock",
            "--delay",
            "0",
            "--no-spin",
            "--quiet",
            "--invert-scroll",
            "--scroll-scale",
            "0.5",
        ],
    )

    assert result.exit_code == 0
    assert ScrollPlaybackBackend.injected_scrolls == [(0, 1)]


@pytest.mark.parametrize("scale", ["0", "-1", "nan", "inf", "101"])
def test_play_rejects_invalid_scroll_scale(tmp_path: object, scale: str) -> None:
    session_file = str(tmp_path) + "/scroll.ctrk"
    _write_scroll_session(session_file, [(0, -1)])

    result = runner.invoke(
        app,
        [
            "play",
            session_file,
            "--backend",
            "mock",
            "--scroll-scale",
            scale,
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "--scroll-scale" in result.output
