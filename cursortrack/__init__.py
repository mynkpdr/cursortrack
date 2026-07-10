"""CursorTrack package root exposing Session and Backend APIs."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from cursortrack.backends import get_backend
from cursortrack.backends.base import InputBackend
from cursortrack.core.layout import (
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    MonitorLayout,
    Rect,
    Scale,
    ScrollUnit,
)
from cursortrack.core.playback import (
    CompatibilityFinding,
    CompatibilityReport,
    MappingMode,
    PlaybackMapping,
    TransformError,
    assess_playback,
    map_point,
)
from cursortrack.core.session import DecodeLimits, Session

try:
    __version__ = version("cursortrack")
except PackageNotFoundError:
    # Package is not installed (e.g. running directly from a source checkout).
    __version__ = "0.0.0.dev0"

__all__ = [
    "CompatibilityFinding",
    "CompatibilityReport",
    "CoordinateUnit",
    "DecodeLimits",
    "DesktopLayout",
    "InputBackend",
    "InputCapabilities",
    "MappingMode",
    "MonitorLayout",
    "PlaybackMapping",
    "Rect",
    "Scale",
    "ScrollUnit",
    "Session",
    "TransformError",
    "assess_playback",
    "get_backend",
    "map_point",
]
