"""Unit tests for the playback fail-safe corner detection.

These exercise the pure `_is_in_corner` helper directly (no display or
backend required) so they run on any CI platform, including the negative
virtual-screen origins produced by monitors placed left of or above the
primary one.
"""

from __future__ import annotations

import pytest

from cursortrack.cli.play import _is_in_corner

# Single-monitor bounds: origin at (0, 0), 1920x1080.
SINGLE_MONITOR = (0, 0, 1920, 1080)
# Multi-monitor bounds where a secondary display sits left of and above the
# primary one, giving the virtual desktop a negative origin.
MULTI_MONITOR_NEGATIVE_ORIGIN = (-1920, -200, 3840, 1280)


@pytest.mark.parametrize(
    "x,y",
    [
        (0, 0),
        (5, 5),
        (1919, 0),
        (1914, 5),
        (0, 1079),
        (5, 1074),
        (1919, 1079),
        (1914, 1074),
    ],
)
def test_corner_within_tolerance_on_single_monitor(x: int, y: int) -> None:
    ox, oy, w, h = SINGLE_MONITOR
    assert _is_in_corner(x, y, ox, oy, w, h)


@pytest.mark.parametrize("x,y", [(960, 540), (6, 6), (1913, 6), (6, 1073), (1913, 1073)])
def test_center_and_near_misses_are_not_corners_on_single_monitor(x: int, y: int) -> None:
    ox, oy, w, h = SINGLE_MONITOR
    assert not _is_in_corner(x, y, ox, oy, w, h)


def test_top_left_corner_with_negative_origin() -> None:
    """A secondary monitor left-of/above the primary shifts the virtual origin negative."""
    ox, oy, w, h = MULTI_MONITOR_NEGATIVE_ORIGIN
    assert _is_in_corner(ox, oy, ox, oy, w, h)
    assert _is_in_corner(ox + 5, oy + 5, ox, oy, w, h)


def test_bottom_right_corner_with_negative_origin() -> None:
    ox, oy, w, h = MULTI_MONITOR_NEGATIVE_ORIGIN
    assert _is_in_corner(ox + w - 1, oy + h - 1, ox, oy, w, h)
    assert _is_in_corner(ox + w - 6, oy + h - 6, ox, oy, w, h)


def test_center_of_negative_origin_desktop_is_not_a_corner() -> None:
    ox, oy, w, h = MULTI_MONITOR_NEGATIVE_ORIGIN
    cx, cy = ox + w // 2, oy + h // 2
    assert not _is_in_corner(cx, cy, ox, oy, w, h)


def test_origin_at_zero_zero_is_not_a_false_corner_for_negative_origin_desktop() -> None:
    """Regression: with a legacy (0, 0)-anchored corner check, a point sitting at the
    *old* primary-monitor origin would wrongly read as "in a corner" on a desktop
    whose real virtual origin has since shifted negative. It must not trigger here.
    """
    ox, oy, w, h = MULTI_MONITOR_NEGATIVE_ORIGIN
    assert not _is_in_corner(0, 0, ox, oy, w, h)
