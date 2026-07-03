"""CursorTrack package root exposing Session and Backend APIs."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from cursortrack.backends import get_backend
from cursortrack.backends.base import InputBackend
from cursortrack.core.session import Session

try:
    __version__ = version("cursortrack")
except PackageNotFoundError:
    # Package is not installed (e.g. running directly from a source checkout).
    __version__ = "0.0.0.dev0"

__all__ = ["InputBackend", "Session", "get_backend"]
