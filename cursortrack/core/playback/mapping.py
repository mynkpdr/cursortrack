"""Explicit, opt-in coordinate mapping modes for portable replay."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MappingMode(str, Enum):
    """How recorded coordinates are translated onto the target desktop.

    None of these modes silently clamp. Out-of-range results stay out of range
    and are reported by compatibility assessment when they can be detected.
    """

    ABSOLUTE = "absolute"
    SCALE_TO_BOUNDS = "scale-to-bounds"
    OFFSET = "offset"
    TARGET_MONITOR = "target-monitor"


@dataclass(frozen=True)
class PlaybackMapping:
    """User-selected transform policy. Defaults never invent a remapping."""

    mode: MappingMode = MappingMode.ABSOLUTE
    offset_x: int = 0
    offset_y: int = 0
    source_monitor: str | None = None
    target_monitor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, MappingMode):
            raise ValueError("mode must be a MappingMode.")
        if type(self.offset_x) is not int or type(self.offset_y) is not int:
            raise ValueError("offset_x and offset_y must be integers.")
        if self.mode is MappingMode.OFFSET and self.offset_x == 0 and self.offset_y == 0:
            raise ValueError("offset mapping requires a nonzero --offset-x or --offset-y.")
        if self.mode is MappingMode.TARGET_MONITOR:
            if not self.source_monitor or not self.target_monitor:
                raise ValueError(
                    "target-monitor mapping requires --source-monitor and --target-monitor."
                )
        elif self.source_monitor is not None or self.target_monitor is not None:
            raise ValueError(
                "source/target monitor ids are only valid with target-monitor mapping."
            )
        if self.mode is not MappingMode.OFFSET and (self.offset_x or self.offset_y):
            raise ValueError("offset_x/offset_y are only valid with offset mapping.")
