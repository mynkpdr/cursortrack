"""Tests for shared CLI output safety helpers."""

from __future__ import annotations

import os

import pytest

from cursortrack.cli._io import AtomicOutput


def test_parent_directory_fsync_cleanup_is_best_effort(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows may invalidate a directory descriptor when fsync rejects it."""
    output = AtomicOutput(str(tmp_path) + "/destination.txt")

    def fail(_fd: int) -> None:
        raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr(os, "open", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(os, "fsync", fail)
    monkeypatch.setattr(os, "close", fail)

    output._fsync_parent()
    output.discard()
