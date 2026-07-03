"""Shared terminal-output formatting helpers used by multiple CLI subcommands."""

from __future__ import annotations

from cursortrack.core.events import CAP_CLICK, CAP_MOVE, CAP_SCROLL, CAP_TOUCH


def format_hms(s: float) -> str:
    """Format seconds into HH:MM:SS string."""
    seconds = int(s)
    return "%02d:%02d:%02d" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def format_size(n: float) -> str:
    """Format bytes into readable size."""
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.1f}{unit}"
        val /= 1024.0
    return f"{val:.1f}B"


def get_capture_names(mask: int) -> list[str]:
    """Retrieve string names of active capture flags from bitmask."""
    names = []
    if mask & CAP_MOVE:
        names.append("move")
    if mask & CAP_CLICK:
        names.append("click")
    if mask & CAP_SCROLL:
        names.append("scroll")
    if mask & CAP_TOUCH:
        names.append("touch")
    return names
