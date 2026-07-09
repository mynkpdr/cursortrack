"""Tests for the Windows Win32 backend's virtual-screen metrics.

These require an actual Windows host (ctypes.windll and user32 only exist
there), so they skip everywhere else rather than fail.
"""

from __future__ import annotations

import sys

import pytest

IS_WINDOWS = sys.platform.startswith("win")

requires_windows = pytest.mark.skipif(
    not IS_WINDOWS, reason="Requires a Windows host (ctypes.windll is Windows-only)."
)


@requires_windows
def test_virtual_screen_metrics_are_positive() -> None:
    from cursortrack.backends.windows import WindowsBackend

    backend = WindowsBackend()
    width, height = backend.get_screen_size()
    assert width > 0
    assert height > 0


@requires_windows
def test_screen_bounds_are_consistent_with_screen_size() -> None:
    from cursortrack.backends.windows import WindowsBackend

    backend = WindowsBackend()
    ox, oy, w, h = backend.get_screen_bounds()
    assert (w, h) == backend.get_screen_size()
    assert isinstance(ox, int)
    assert isinstance(oy, int)


@requires_windows
def test_get_screen_bounds_matches_default_shape() -> None:
    from cursortrack.backends.windows import WindowsBackend

    backend = WindowsBackend()
    bounds = backend.get_screen_bounds()
    assert len(bounds) == 4
    assert all(isinstance(v, int) for v in bounds)
