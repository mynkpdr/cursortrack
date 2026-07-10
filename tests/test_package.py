"""Tests for installed package metadata and typing support."""

from __future__ import annotations

from importlib.resources import files


def test_pep561_marker_is_packaged() -> None:
    assert files("cursortrack").joinpath("py.typed").is_file()
