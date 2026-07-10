"""Tests for playback compatibility assessment."""

from __future__ import annotations

from cursortrack.core.events import ButtonEvent, MoveEvent, ScrollEvent, TapEvent
from cursortrack.core.layout import (
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    MonitorLayout,
    Rect,
    ScrollUnit,
)
from cursortrack.core.playback import MappingMode, PlaybackMapping, assess_playback
from cursortrack.core.session import Session


def _session(
    events: list[object],
    *,
    scr_w: int = 1920,
    scr_h: int = 1080,
    integrity: str = "complete",
    version: int = 2,
) -> Session:
    header = {
        "version": version,
        "codec": 0,
        "rate": 100,
        "scr_w": scr_w,
        "scr_h": scr_h,
        "start": 0.0,
        "x0": 0,
        "y0": 0,
        "capture": 7,
    }
    return Session(header, events, integrity=integrity)  # type: ignore[arg-type]


def _target(
    width: int = 1920,
    height: int = 1080,
    *,
    unit: CoordinateUnit = CoordinateUnit.PHYSICAL_PIXEL,
) -> tuple[DesktopLayout, InputCapabilities]:
    bounds = Rect(0, 0, width, height)
    layout = DesktopLayout(
        known=True,
        coordinate_unit=unit,
        bounds=bounds,
        monitors=(MonitorLayout(id="virtual-desktop", primary=True, bounds=bounds),),
    )
    caps = InputCapabilities(
        coordinate_unit=unit,
        buttons=("left", "right", "middle", "x1", "x2"),
        scroll_units=(ScrollUnit.WHEEL_DETENT,),
        read_position=True,
        inject_position=True,
        inject_buttons=True,
        inject_scroll=True,
    )
    return layout, caps


def test_matching_bounds_allow_absolute_with_legacy_warning() -> None:
    session = _session(
        [
            MoveEvent(0, 10, 20),
            ButtonEvent(1, 10, 20, "left", True),
            ButtonEvent(2, 10, 20, "left", False),
        ]
    )
    layout, caps = _target()
    report = assess_playback(session, layout, caps, strict=True)
    assert report.ok
    assert any(f.code in {"insufficient-metadata", "legacy-absolute"} for f in report.warnings)
    assert any(f.code == "unit-unproven" for f in report.warnings)


def test_strict_refuses_layout_mismatch_without_explicit_mapping() -> None:
    session = _session([MoveEvent(0, 10, 20)])
    layout, caps = _target(2560, 1440)
    report = assess_playback(session, layout, caps, strict=True)
    assert not report.ok
    assert any(f.code == "layout-mismatch" for f in report.errors)


def test_scale_to_bounds_allows_different_sizes() -> None:
    session = _session([MoveEvent(0, 10, 20)])
    layout, caps = _target(2560, 1440)
    mapping = PlaybackMapping(mode=MappingMode.SCALE_TO_BOUNDS)
    report = assess_playback(session, layout, caps, mapping, strict=True)
    assert report.ok
    assert any(f.code == "scale-explicit" for f in report.warnings)


def test_strict_refuses_missing_button_capability() -> None:
    session = _session(
        [
            ButtonEvent(0, 1, 1, "x2", True),
            ButtonEvent(1, 1, 1, "x2", False),
        ]
    )
    layout, _ = _target()
    caps = InputCapabilities(
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        buttons=("left", "right"),
        read_position=True,
        inject_position=True,
        inject_buttons=True,
    )
    report = assess_playback(session, layout, caps, strict=True)
    assert not report.ok
    assert any(f.code == "button-capability" for f in report.errors)


def test_strict_refuses_touch_and_permissive_warns() -> None:
    session = _session([TapEvent(0, 5, 5, touch_id=1)])
    layout, caps = _target()
    strict = assess_playback(session, layout, caps, strict=True)
    assert not strict.ok
    permissive = assess_playback(session, layout, caps, strict=False)
    assert permissive.ok
    assert any(f.code == "touch-legacy" for f in permissive.warnings)


def test_scroll_requires_wheel_detent_capability() -> None:
    session = _session([ScrollEvent(0, 1, 1, 0, 1)])
    layout, _ = _target()
    caps = InputCapabilities(
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        buttons=("left",),
        scroll_units=(),
        read_position=True,
        inject_position=True,
        inject_scroll=True,
    )
    report = assess_playback(session, layout, caps, strict=True)
    assert not report.ok
    assert any(f.code == "scroll-unit" for f in report.errors)


def test_zero_screen_size_is_insufficient_metadata() -> None:
    session = _session([MoveEvent(0, 1, 1)], scr_w=0, scr_h=0)
    assert session.layout_metadata_sufficient is False
    assert session.source_layout().known is False


def test_invalid_button_state_blocks_strict_playback() -> None:
    session = _session([ButtonEvent(0, 1, 1, "left", False)])
    assert session.button_state_valid is False
    layout, caps = _target()
    report = assess_playback(session, layout, caps, strict=True)
    assert not report.ok
    assert any(f.code == "button-state" for f in report.errors)


def test_unknown_button_is_never_silently_treated_as_supported() -> None:
    session = _session(
        [
            ButtonEvent(0, 1, 1, "button99", True),
            ButtonEvent(1, 1, 1, "button99", False),
        ]
    )
    layout, caps = _target()

    report = assess_playback(session, layout, caps, strict=False)

    assert not report.ok
    assert any(f.code == "button-unknown" for f in report.errors)


def test_button_events_require_injection_capability_not_just_names() -> None:
    session = _session(
        [
            ButtonEvent(0, 1, 1, "left", True),
            ButtonEvent(1, 1, 1, "left", False),
        ]
    )
    layout, _ = _target()
    caps = InputCapabilities(
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        buttons=("left",),
        read_position=True,
        inject_position=True,
        inject_buttons=False,
    )

    report = assess_playback(session, layout, caps, strict=False)

    assert not report.ok
    assert any(f.code == "button-inject" for f in report.errors)


def test_strict_refuses_mapped_points_outside_known_target() -> None:
    session = _session([MoveEvent(0, 95, 50)], scr_w=100, scr_h=100)
    layout, caps = _target(100, 100)
    mapping = PlaybackMapping(mode=MappingMode.OFFSET, offset_x=10)

    strict = assess_playback(session, layout, caps, mapping, strict=True)
    assert not strict.ok
    assert any(f.code == "mapped-outside-target" for f in strict.errors)

    permissive = assess_playback(session, layout, caps, mapping, strict=False)
    assert permissive.ok
    assert any(f.code == "mapped-outside-target" for f in permissive.warnings)


def test_target_restrictions_are_visible_in_compatibility_report() -> None:
    session = _session([MoveEvent(0, 10, 20)])
    layout, _ = _target()
    caps = InputCapabilities(
        coordinate_unit=CoordinateUnit.PHYSICAL_PIXEL,
        buttons=("left",),
        scroll_units=(ScrollUnit.WHEEL_DETENT,),
        read_position=True,
        inject_position=True,
        inject_buttons=True,
        inject_scroll=True,
        restrictions=("interactive-desktop-only",),
    )

    report = assess_playback(session, layout, caps)

    assert any(
        finding.code == "target-restriction" and "interactive-desktop-only" in finding.message
        for finding in report.warnings
    )
