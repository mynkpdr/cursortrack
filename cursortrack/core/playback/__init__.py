"""Pure playback policy: compatibility assessment and explicit coordinate mapping."""

from __future__ import annotations

from cursortrack.core.playback.compat import (
    CompatibilityFinding,
    CompatibilityReport,
    assess_playback,
)
from cursortrack.core.playback.mapping import MappingMode, PlaybackMapping
from cursortrack.core.playback.transform import TransformError, map_point

__all__ = [
    "CompatibilityFinding",
    "CompatibilityReport",
    "MappingMode",
    "PlaybackMapping",
    "TransformError",
    "assess_playback",
    "map_point",
]
