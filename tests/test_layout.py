"""Tests for portable desktop-layout and input-capability models."""

from __future__ import annotations

import pytest

from cursortrack import (
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    MonitorLayout,
    Rect,
    Scale,
    ScrollUnit,
)


def _known_layout() -> DesktopLayout:
    monitors = (
        MonitorLayout(
            id="source-0",
            primary=False,
            bounds=Rect(-1920, 0, 1920, 1080),
        ),
        MonitorLayout(
            id="source-1",
            primary=True,
            bounds=Rect(0, 0, 2560, 1440),
            scale=Scale(5, 4),
        ),
    )
    return DesktopLayout(
        known=True,
        coordinate_unit=CoordinateUnit.BACKEND_UNIT,
        coordinate_unit_id="x11-root-v1",
        bounds=Rect(-1920, 0, 4480, 1440),
        monitors=monitors,
    )


def test_known_layout_preserves_negative_origin_and_monitor_union() -> None:
    layout = _known_layout()

    assert layout.bounds == Rect(-1920, 0, 4480, 1440)
    assert layout.primary_monitor.id == "source-1"
    assert layout.monitor_ids == ("source-0", "source-1")


def test_unknown_layout_can_retain_a_known_coordinate_unit() -> None:
    layout = DesktopLayout.unknown(CoordinateUnit.LOGICAL_POINT)

    assert layout.known is False
    assert layout.coordinate_unit is CoordinateUnit.LOGICAL_POINT
    assert layout.bounds is None
    assert layout.monitors is None


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Rect(0, 0, 0, 10),
        lambda: Scale(2, 4),
        lambda: Scale(1, 0),
        lambda: MonitorLayout(id="", primary=True, bounds=Rect(0, 0, 10, 10)),
        lambda: MonitorLayout(
            id="x" * 65,
            primary=True,
            bounds=Rect(0, 0, 10, 10),
        ),
    ],
)
def test_invalid_layout_components_are_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_backend_coordinate_unit_requires_an_identifier() -> None:
    with pytest.raises(ValueError, match="coordinate_unit_id"):
        DesktopLayout.unknown(CoordinateUnit.BACKEND_UNIT)

    with pytest.raises(ValueError, match="coordinate_unit_id"):
        DesktopLayout.unknown(
            CoordinateUnit.PHYSICAL_PIXEL,
            coordinate_unit_id="unexpected",
        )


def test_known_layout_requires_one_primary_unique_ids_and_matching_bounds() -> None:
    primary = MonitorLayout("display", True, Rect(0, 0, 100, 100))

    with pytest.raises(ValueError, match="exactly one primary"):
        DesktopLayout(
            known=True,
            coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
            bounds=Rect(0, 0, 100, 100),
            monitors=(MonitorLayout("display", False, Rect(0, 0, 100, 100)),),
        )

    with pytest.raises(ValueError, match="unique"):
        DesktopLayout(
            known=True,
            coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
            bounds=Rect(0, 0, 200, 100),
            monitors=(primary, MonitorLayout("display", False, Rect(100, 0, 100, 100))),
        )

    with pytest.raises(ValueError, match="bounding rectangle"):
        DesktopLayout(
            known=True,
            coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
            bounds=Rect(0, 0, 200, 100),
            monitors=(primary,),
        )


def test_input_capabilities_validate_canonical_buttons_and_operations() -> None:
    capabilities = InputCapabilities(
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        buttons=("left", "right", "middle"),
        scroll_units=(ScrollUnit.WHEEL_DETENT,),
        read_position=True,
        inject_position=True,
        inject_buttons=True,
        inject_scroll=True,
        capture_buttons=False,
        capture_scroll=False,
        restrictions=("interactive-desktop-only",),
    )

    assert capabilities.supports_button("right")
    assert capabilities.supports_scroll(ScrollUnit.WHEEL_DETENT)
    assert capabilities.can_play_pointer is True

    with pytest.raises(ValueError, match="canonical order"):
        InputCapabilities(buttons=("right", "left"))

    with pytest.raises(ValueError, match="unknown button"):
        InputCapabilities(buttons=("button99",))
