"""Pure coordinate transforms. Never clamp or invent a mapping."""

from __future__ import annotations

from cursortrack.core.layout import DesktopLayout, MonitorLayout, Rect
from cursortrack.core.playback.mapping import MappingMode, PlaybackMapping


class TransformError(ValueError):
    """Raised when an explicit mapping cannot be applied to a point or layout."""


def _require_known(layout: DesktopLayout, label: str) -> tuple[Rect, tuple[MonitorLayout, ...]]:
    if not layout.known or layout.bounds is None or not layout.monitors:
        raise TransformError(f"{label} layout must be known to apply this mapping.")
    return layout.bounds, layout.monitors


def _monitor_by_id(layout: DesktopLayout, monitor_id: str, label: str) -> MonitorLayout:
    _, monitors = _require_known(layout, label)
    for monitor in monitors:
        if monitor.id == monitor_id:
            return monitor
    raise TransformError(f"{label} layout has no monitor id {monitor_id!r}.")


def _scale_axis(value: int, src_origin: int, src_span: int, dst_origin: int, dst_span: int) -> int:
    # Integer-only linear map. Floor division matches "never invent subpixel
    # precision"; callers that need exact round-trips must use matching spans.
    return dst_origin + (value - src_origin) * dst_span // src_span


def map_point(
    x: int,
    y: int,
    source: DesktopLayout,
    target: DesktopLayout,
    mapping: PlaybackMapping,
) -> tuple[int, int]:
    """Map one recorded point into the target desktop under an explicit policy."""
    if mapping.mode is MappingMode.ABSOLUTE:
        return x, y

    if mapping.mode is MappingMode.OFFSET:
        return x + mapping.offset_x, y + mapping.offset_y

    if mapping.mode is MappingMode.SCALE_TO_BOUNDS:
        src_bounds, _ = _require_known(source, "source")
        dst_bounds, _ = _require_known(target, "target")
        return (
            _scale_axis(x, src_bounds.x, src_bounds.width, dst_bounds.x, dst_bounds.width),
            _scale_axis(y, src_bounds.y, src_bounds.height, dst_bounds.y, dst_bounds.height),
        )

    if mapping.mode is MappingMode.TARGET_MONITOR:
        assert mapping.source_monitor is not None
        assert mapping.target_monitor is not None
        src_mon = _monitor_by_id(source, mapping.source_monitor, "source")
        dst_mon = _monitor_by_id(target, mapping.target_monitor, "target")
        return (
            _scale_axis(
                x,
                src_mon.bounds.x,
                src_mon.bounds.width,
                dst_mon.bounds.x,
                dst_mon.bounds.width,
            ),
            _scale_axis(
                y,
                src_mon.bounds.y,
                src_mon.bounds.height,
                dst_mon.bounds.y,
                dst_mon.bounds.height,
            ),
        )

    raise TransformError(f"Unsupported mapping mode: {mapping.mode}")
