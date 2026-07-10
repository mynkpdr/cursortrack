"""Portable desktop-layout and input-capability value models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
UINT32_MAX = (1 << 32) - 1
MAX_IDENTIFIER_BYTES = 64

CANONICAL_BUTTONS = ("left", "right", "middle", "x1", "x2")


class CoordinateUnit(str, Enum):
    UNKNOWN = "unknown"
    PHYSICAL_PIXEL = "physical-pixel"
    LOGICAL_POINT = "logical-point"
    BACKEND_UNIT = "backend-unit"


class ScrollUnit(str, Enum):
    WHEEL_DETENT = "wheel-detent"
    BACKEND_UNIT = "backend-unit"


def _require_plain_int(value: int, label: str) -> None:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer.")


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise ValueError(f"{label} must be a nonempty UTF-8 string of at most 64 bytes.")


def _validate_coordinate_unit(unit: CoordinateUnit, unit_id: str | None) -> None:
    if not isinstance(unit, CoordinateUnit):
        raise ValueError("coordinate_unit must be a CoordinateUnit.")
    if unit is CoordinateUnit.BACKEND_UNIT:
        if unit_id is None:
            raise ValueError("backend-unit requires coordinate_unit_id.")
        _validate_identifier(unit_id, "coordinate_unit_id")
    elif unit_id is not None:
        raise ValueError("coordinate_unit_id is only valid for backend-unit.")


@dataclass(frozen=True)
class Rect:
    """Half-open rectangle in one declared desktop coordinate unit."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for label, value in (("x", self.x), ("y", self.y)):
            _require_plain_int(value, label)
            if not INT32_MIN <= value <= INT32_MAX:
                raise ValueError(f"{label} must fit signed 32-bit range.")
        for label, value in (("width", self.width), ("height", self.height)):
            _require_plain_int(value, label)
            if not 1 <= value <= UINT32_MAX:
                raise ValueError(f"{label} must be 1..{UINT32_MAX}.")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @classmethod
    def bounding(cls, rectangles: tuple[Rect, ...]) -> Rect:
        if not rectangles:
            raise ValueError("At least one rectangle is required.")
        left = min(rect.x for rect in rectangles)
        top = min(rect.y for rect in rectangles)
        right = max(rect.right for rect in rectangles)
        bottom = max(rect.bottom for rect in rectangles)
        return cls(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class Scale:
    """Reduced physical-pixels-per-logical-point ratio."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        for label, value in (("numerator", self.numerator), ("denominator", self.denominator)):
            _require_plain_int(value, label)
            if not 1 <= value <= UINT32_MAX:
                raise ValueError(f"{label} must be 1..{UINT32_MAX}.")
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("Scale must be reduced to lowest terms.")


@dataclass(frozen=True)
class MonitorLayout:
    """One file-local monitor description."""

    id: str
    primary: bool
    bounds: Rect
    scale: Scale | None = None
    rotation: int = 0

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "Monitor id")
        if type(self.primary) is not bool:
            raise ValueError("primary must be a boolean.")
        if not isinstance(self.bounds, Rect):
            raise ValueError("bounds must be a Rect.")
        if self.scale is not None and not isinstance(self.scale, Scale):
            raise ValueError("scale must be a Scale or None.")
        _require_plain_int(self.rotation, "rotation")
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError("rotation must be 0, 90, 180, or 270.")


@dataclass(frozen=True)
class DesktopLayout:
    """Known monitor topology or an explicit unknown state."""

    known: bool
    coordinate_unit: CoordinateUnit
    coordinate_unit_id: str | None = None
    bounds: Rect | None = None
    monitors: tuple[MonitorLayout, ...] | None = None

    def __post_init__(self) -> None:
        if type(self.known) is not bool:
            raise ValueError("known must be a boolean.")
        _validate_coordinate_unit(self.coordinate_unit, self.coordinate_unit_id)

        if not self.known:
            if self.bounds is not None or self.monitors is not None:
                raise ValueError("Unknown layout cannot contain bounds or monitors.")
            return

        if self.bounds is None or not self.monitors:
            raise ValueError("Known layout requires bounds and at least one monitor.")
        if not isinstance(self.bounds, Rect):
            raise ValueError("bounds must be a Rect.")
        if any(not isinstance(monitor, MonitorLayout) for monitor in self.monitors):
            raise ValueError("monitors must contain MonitorLayout values.")

        ids = tuple(monitor.id for monitor in self.monitors)
        if len(set(ids)) != len(ids):
            raise ValueError("Monitor ids must be unique.")
        if sum(monitor.primary for monitor in self.monitors) != 1:
            raise ValueError("Known layout requires exactly one primary monitor.")
        expected_bounds = Rect.bounding(tuple(monitor.bounds for monitor in self.monitors))
        if self.bounds != expected_bounds:
            raise ValueError("Desktop bounds must equal the monitor bounding rectangle.")

    @classmethod
    def unknown(
        cls,
        coordinate_unit: CoordinateUnit = CoordinateUnit.UNKNOWN,
        coordinate_unit_id: str | None = None,
    ) -> DesktopLayout:
        return cls(
            known=False,
            coordinate_unit=coordinate_unit,
            coordinate_unit_id=coordinate_unit_id,
        )

    @property
    def primary_monitor(self) -> MonitorLayout:
        if not self.monitors:
            raise RuntimeError("Unknown layout has no primary monitor.")
        return next(monitor for monitor in self.monitors if monitor.primary)

    @property
    def monitor_ids(self) -> tuple[str, ...]:
        return tuple(monitor.id for monitor in self.monitors or ())


@dataclass(frozen=True)
class InputCapabilities:
    """Backend facts used by future strict playback negotiation."""

    coordinate_unit: CoordinateUnit = CoordinateUnit.UNKNOWN
    coordinate_unit_id: str | None = None
    buttons: tuple[str, ...] = ()
    scroll_units: tuple[ScrollUnit, ...] = ()
    precise_scroll: bool = False
    read_position: bool = False
    inject_position: bool = False
    inject_buttons: bool = False
    inject_scroll: bool = False
    capture_buttons: bool = False
    capture_scroll: bool = False
    restrictions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_coordinate_unit(self.coordinate_unit, self.coordinate_unit_id)

        unknown_buttons = set(self.buttons) - set(CANONICAL_BUTTONS)
        if unknown_buttons:
            raise ValueError(
                f"InputCapabilities contains unknown button: {sorted(unknown_buttons)}"
            )
        expected_buttons = tuple(button for button in CANONICAL_BUTTONS if button in self.buttons)
        if self.buttons != expected_buttons:
            raise ValueError("buttons must be unique and in canonical order.")

        if any(not isinstance(unit, ScrollUnit) for unit in self.scroll_units):
            raise ValueError("scroll_units must contain ScrollUnit values.")
        if len(set(self.scroll_units)) != len(self.scroll_units):
            raise ValueError("scroll_units must be unique.")

        boolean_fields = (
            "precise_scroll",
            "read_position",
            "inject_position",
            "inject_buttons",
            "inject_scroll",
            "capture_buttons",
            "capture_scroll",
        )
        for field in boolean_fields:
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} must be a boolean.")

        if len(set(self.restrictions)) != len(self.restrictions):
            raise ValueError("restrictions must be unique.")
        for restriction in self.restrictions:
            _validate_identifier(restriction, "restriction")

    def supports_button(self, button: str) -> bool:
        return button in self.buttons

    def supports_scroll(self, unit: ScrollUnit) -> bool:
        return unit in self.scroll_units

    @property
    def can_play_pointer(self) -> bool:
        return self.read_position and self.inject_position
