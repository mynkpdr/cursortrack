"""Tests for shared CLI output safety helpers."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from cursortrack.cli._io import AtomicOutput


def test_atomic_commit_uses_windows_compatible_writable_descriptor() -> None:
    output = object.__new__(AtomicOutput)
    output.destination = "/tmp/destination"
    output.path = "/tmp/replacement"
    output._active = True

    with (
        mock.patch("cursortrack.cli._io.open", mock.mock_open()) as open_file,
        mock.patch("cursortrack.cli._io.os.fsync"),
        mock.patch("cursortrack.cli._io.os.path.exists", return_value=False),
        mock.patch("cursortrack.cli._io.os.replace"),
        mock.patch.object(output, "_fsync_parent"),
    ):
        output.commit()

    open_file.assert_called_once_with(output.path, "rb+")


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
