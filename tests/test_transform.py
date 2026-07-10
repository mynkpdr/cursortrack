"""Tests for pure coordinate transforms and mapping configuration."""

from __future__ import annotations

import random

import pytest

from cursortrack.core.layout import CoordinateUnit, DesktopLayout, MonitorLayout, Rect
from cursortrack.core.playback import MappingMode, PlaybackMapping, TransformError, map_point


def _layout(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    unit: CoordinateUnit = CoordinateUnit.PHYSICAL_PIXEL,
    monitor_id: str = "primary",
    unit_id: str | None = None,
) -> DesktopLayout:
    bounds = Rect(x, y, width, height)
    return DesktopLayout(
        known=True,
        coordinate_unit=unit,
        coordinate_unit_id=unit_id,
        bounds=bounds,
        monitors=(MonitorLayout(id=monitor_id, primary=True, bounds=bounds),),
    )


def test_absolute_is_identity() -> None:
    source = _layout(0, 0, 1920, 1080)
    target = _layout(0, 0, 2560, 1440)
    assert map_point(100, 200, source, target, PlaybackMapping()) == (100, 200)


def test_offset_shifts_without_clamping() -> None:
    source = _layout(0, 0, 100, 100)
    target = _layout(0, 0, 100, 100)
    mapping = PlaybackMapping(mode=MappingMode.OFFSET, offset_x=-50, offset_y=10)
    assert map_point(0, 0, source, target, mapping) == (-50, 10)
    assert map_point(120, 5, source, target, mapping) == (70, 15)


def test_scale_to_bounds_is_linear_and_uncamped() -> None:
    source = _layout(0, 0, 100, 100)
    target = _layout(10, 20, 200, 400)
    mapping = PlaybackMapping(mode=MappingMode.SCALE_TO_BOUNDS)
    assert map_point(0, 0, source, target, mapping) == (10, 20)
    assert map_point(50, 25, source, target, mapping) == (110, 120)
    # Outside source bounds still maps; never clamped to the target rectangle.
    assert map_point(150, -10, source, target, mapping) == (310, -20)


def test_target_monitor_maps_relative_position() -> None:
    source = DesktopLayout(
        known=True,
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        bounds=Rect(0, 0, 200, 100),
        monitors=(
            MonitorLayout(id="left", primary=True, bounds=Rect(0, 0, 100, 100)),
            MonitorLayout(id="right", primary=False, bounds=Rect(100, 0, 100, 100)),
        ),
    )
    target = DesktopLayout(
        known=True,
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        bounds=Rect(0, 0, 400, 200),
        monitors=(
            MonitorLayout(id="a", primary=True, bounds=Rect(0, 0, 200, 200)),
            MonitorLayout(id="b", primary=False, bounds=Rect(200, 0, 200, 200)),
        ),
    )
    mapping = PlaybackMapping(
        mode=MappingMode.TARGET_MONITOR,
        source_monitor="right",
        target_monitor="b",
    )
    assert map_point(150, 25, source, target, mapping) == (300, 50)


def test_scale_requires_known_layouts() -> None:
    with pytest.raises(TransformError, match="known"):
        map_point(
            1,
            1,
            DesktopLayout.unknown(),
            _layout(0, 0, 10, 10),
            PlaybackMapping(mode=MappingMode.SCALE_TO_BOUNDS),
        )


def test_mapping_config_rejects_inconsistent_options() -> None:
    with pytest.raises(ValueError, match="nonzero"):
        PlaybackMapping(mode=MappingMode.OFFSET)
    with pytest.raises(ValueError, match="target-monitor"):
        PlaybackMapping(mode=MappingMode.TARGET_MONITOR, source_monitor="a")
    with pytest.raises(ValueError, match="only valid with offset"):
        PlaybackMapping(mode=MappingMode.ABSOLUTE, offset_x=1)


@pytest.mark.parametrize("seed", range(8))
def test_scale_roundtrip_property_when_spans_match(seed: int) -> None:
    """Scaling onto an identical rectangle is an identity (property-style)."""
    rng = random.Random(seed)
    width = rng.randint(1, 4000)
    height = rng.randint(1, 4000)
    origin_x = rng.randint(-2000, 2000)
    origin_y = rng.randint(-2000, 2000)
    layout = _layout(origin_x, origin_y, width, height)
    mapping = PlaybackMapping(mode=MappingMode.SCALE_TO_BOUNDS)
    for _ in range(20):
        x = rng.randint(origin_x - width, origin_x + 2 * width)
        y = rng.randint(origin_y - height, origin_y + 2 * height)
        assert map_point(x, y, layout, layout, mapping) == (x, y)
