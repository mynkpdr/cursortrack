"""Source/target compatibility assessment for safe playback."""

from __future__ import annotations

from dataclasses import dataclass

from cursortrack.core.events import ButtonEvent, ScrollEvent, TapEvent
from cursortrack.core.layout import (
    CANONICAL_BUTTONS,
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    ScrollUnit,
)
from cursortrack.core.playback.mapping import MappingMode, PlaybackMapping
from cursortrack.core.playback.transform import TransformError, map_point
from cursortrack.core.session import Session


@dataclass(frozen=True)
class CompatibilityFinding:
    """One warning or blocking error from playback negotiation."""

    code: str
    message: str
    blocking: bool


@dataclass(frozen=True)
class CompatibilityReport:
    """Result of comparing a session against a target desktop under a mapping."""

    ok: bool
    findings: tuple[CompatibilityFinding, ...] = ()
    source_layout: DesktopLayout | None = None
    target_layout: DesktopLayout | None = None
    source_capabilities: InputCapabilities | None = None
    target_capabilities: InputCapabilities | None = None
    mapping: PlaybackMapping | None = None
    metadata_sufficient: bool = True

    @property
    def errors(self) -> tuple[CompatibilityFinding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def warnings(self) -> tuple[CompatibilityFinding, ...]:
        return tuple(f for f in self.findings if not f.blocking)


def _units_compatible(source: DesktopLayout, target: DesktopLayout) -> bool:
    """Return whether absolute mapping may treat the two layouts as same-space.

    Unknown units never claim equivalence with a *different* concrete unit, but
    two unknowns (typical of v1/v2 onto a conservative target) are treated as
    provisionally compatible and surfaced as a warning elsewhere.
    """
    if source.coordinate_unit is CoordinateUnit.UNKNOWN:
        return target.coordinate_unit is CoordinateUnit.UNKNOWN
    if target.coordinate_unit is CoordinateUnit.UNKNOWN:
        return False
    if source.coordinate_unit is CoordinateUnit.BACKEND_UNIT:
        return (
            target.coordinate_unit is CoordinateUnit.BACKEND_UNIT
            and source.coordinate_unit_id == target.coordinate_unit_id
        )
    return source.coordinate_unit is target.coordinate_unit


def _bounds_match(source: DesktopLayout, target: DesktopLayout) -> bool:
    return bool(
        source.known
        and target.known
        and source.bounds is not None
        and target.bounds is not None
        and source.bounds == target.bounds
    )


def _buttons_used(session: Session) -> tuple[str, ...]:
    used: set[str] = set()
    for event in session.events:
        if isinstance(event, ButtonEvent):
            used.add(event.button)
        elif isinstance(event, TapEvent):
            used.add("left")
    return tuple(button for button in CANONICAL_BUTTONS if button in used)


def _unknown_buttons_used(session: Session) -> tuple[str, ...]:
    known = set(CANONICAL_BUTTONS)
    unknown = {
        event.button
        for event in session.events
        if isinstance(event, ButtonEvent) and event.button not in known
    }
    return tuple(sorted(unknown))


def _has_scroll(session: Session) -> bool:
    return any(isinstance(event, ScrollEvent) for event in session.events)


def _has_touch(session: Session) -> bool:
    return any(isinstance(event, TapEvent) for event in session.events)


def assess_playback(
    session: Session,
    target_layout: DesktopLayout,
    target_capabilities: InputCapabilities,
    mapping: PlaybackMapping | None = None,
    *,
    strict: bool = True,
) -> CompatibilityReport:
    """Compare a session to a target desktop without injecting input.

    In ``strict`` mode, blocking findings make ``ok`` false. In permissive mode
    the same findings are demoted to warnings so operators can proceed after an
    explicit review, except for transform configuration errors that cannot be
    applied at all.
    """
    active_mapping = mapping or PlaybackMapping()
    findings: list[CompatibilityFinding] = []

    source_layout = session.source_layout()
    source_caps = session.source_capabilities()
    metadata_sufficient = session.layout_metadata_sufficient

    def add(code: str, message: str, *, blocking: bool) -> None:
        findings.append(CompatibilityFinding(code=code, message=message, blocking=blocking))

    # Integrity / structural gates
    if session.integrity != "complete":
        add(
            "integrity",
            f"Session integrity is {session.integrity!r}; strict playback refuses "
            "non-complete recordings.",
            blocking=True,
        )

    if not session.button_state_valid:
        add(
            "button-state",
            "Session button state is invalid (unbalanced press/release sequence).",
            blocking=True,
        )

    if not metadata_sufficient:
        add(
            "insufficient-metadata",
            "Recording lacks portable layout metadata (v1/v2 or zero screen size). "
            "Cross-machine replay is not guaranteed.",
            blocking=False,
        )
        if active_mapping.mode is MappingMode.ABSOLUTE:
            add(
                "legacy-absolute",
                "Using absolute coordinates from a recording with insufficient layout "
                "metadata. Confirm the target desktop matches the recording machine.",
                blocking=False,
            )

    if not target_capabilities.can_play_pointer:
        add(
            "target-pointer",
            "Target backend cannot read and inject pointer positions.",
            blocking=True,
        )

    for restriction in target_capabilities.restrictions:
        add(
            "target-restriction",
            f"Target backend restriction: {restriction}.",
            blocking=False,
        )

    # Capability negotiation
    unknown_buttons = _unknown_buttons_used(session)
    for button in unknown_buttons:
        add(
            "button-unknown",
            f"Session uses unknown button {button!r}; playback cannot preserve it.",
            blocking=True,
        )

    used_buttons = _buttons_used(session)
    if used_buttons and not target_capabilities.inject_buttons:
        add(
            "button-inject",
            "Session contains button events but the target cannot inject buttons.",
            blocking=True,
        )

    for button in used_buttons:
        if not target_capabilities.supports_button(button):
            add(
                "button-capability",
                f"Target backend cannot inject button {button!r}.",
                blocking=True,
            )

    if _has_scroll(session):
        if not target_capabilities.inject_scroll:
            add(
                "scroll-inject",
                "Session contains scroll events but the target cannot inject scroll.",
                blocking=True,
            )
        elif not target_capabilities.supports_scroll(ScrollUnit.WHEEL_DETENT):
            add(
                "scroll-unit",
                "Session scroll events use wheel-detent units; target does not advertise them.",
                blocking=True,
            )

    if _has_touch(session):
        add(
            "touch-legacy",
            "Session contains tap/touch events. Current backends replay them as left "
            "clicks; true touch injection is unavailable.",
            blocking=strict,
        )

    # Layout / mapping negotiation
    if active_mapping.mode is MappingMode.ABSOLUTE:
        if source_layout.known and target_layout.known:
            if not _bounds_match(source_layout, target_layout):
                add(
                    "layout-mismatch",
                    "Source and target desktop bounds differ; absolute mapping would "
                    "misplace events. Choose scale-to-bounds, offset, or target-monitor.",
                    blocking=True,
                )
            elif not _units_compatible(source_layout, target_layout):
                # v1/v2 unknown-unit source onto a concrete target with matching
                # bounds: allow with a warning rather than pretending units match.
                if source_layout.coordinate_unit is CoordinateUnit.UNKNOWN:
                    add(
                        "unit-unproven",
                        "Source coordinate unit is unknown; absolute mapping assumes "
                        "the target desktop uses the same space as the recording.",
                        blocking=False,
                    )
                else:
                    add(
                        "unit-mismatch",
                        "Source and target coordinate units are incompatible for absolute mapping.",
                        blocking=True,
                    )
        elif source_layout.known != target_layout.known:
            add(
                "layout-unknown",
                "Absolute mapping requires either matching known layouts or an explicit "
                "non-absolute mapping when one side is unknown.",
                blocking=strict and metadata_sufficient,
            )
        elif not source_layout.known and not target_layout.known:
            add(
                "layout-unknown",
                "Both source and target layouts are unknown; absolute mapping cannot "
                "verify coordinate spaces.",
                blocking=strict,
            )
    else:
        # Non-absolute modes always need enough geometry to compute the transform.
        try:
            if session.events:
                map_point(
                    session.events[0].x,
                    session.events[0].y,
                    source_layout,
                    target_layout,
                    active_mapping,
                )
        except TransformError as exc:
            add("mapping-invalid", str(exc), blocking=True)

        if active_mapping.mode is MappingMode.SCALE_TO_BOUNDS:
            if not source_layout.known or not target_layout.known:
                add(
                    "scale-requires-known",
                    "scale-to-bounds requires known source and target layouts.",
                    blocking=True,
                )
            elif source_layout.bounds is not None and target_layout.bounds is not None:
                add(
                    "scale-explicit",
                    "Coordinates will be linearly scaled from source bounds "
                    f"{source_layout.bounds!r} onto target bounds {target_layout.bounds!r}. "
                    "Values are never clamped.",
                    blocking=False,
                )

        if active_mapping.mode is MappingMode.OFFSET:
            add(
                "offset-explicit",
                f"Coordinates will be shifted by ({active_mapping.offset_x}, "
                f"{active_mapping.offset_y}).",
                blocking=False,
            )

        if active_mapping.mode is MappingMode.TARGET_MONITOR:
            add(
                "monitor-explicit",
                f"Coordinates will be mapped from monitor {active_mapping.source_monitor!r} "
                f"onto {active_mapping.target_monitor!r}.",
                blocking=False,
            )

    # Validate every mapped event: a late point must not escape preview just
    # because the first sample happened to fit.
    if not any(f.code == "mapping-invalid" and f.blocking for f in findings):
        for event in session.events:
            try:
                mapped_x, mapped_y = map_point(
                    event.x,
                    event.y,
                    source_layout,
                    target_layout,
                    active_mapping,
                )
            except TransformError as exc:
                add("mapping-invalid", str(exc), blocking=True)
                break
            if target_layout.known and target_layout.bounds is not None:
                bounds = target_layout.bounds
                inside = (
                    bounds.x <= mapped_x < bounds.right and bounds.y <= mapped_y < bounds.bottom
                )
                if not inside:
                    add(
                        "mapped-outside-target",
                        f"Event at ({event.x}, {event.y}) maps to ({mapped_x}, {mapped_y}), "
                        f"outside target bounds {bounds!r}. Values are not clamped.",
                        blocking=strict,
                    )
                    break

    if not strict:
        # Permissive mode keeps hard transform/config failures blocking, but demotes
        # policy refusals so operators can proceed after reviewing the report.
        remapped: list[CompatibilityFinding] = []
        hard = {
            "mapping-invalid",
            "scale-requires-known",
            "target-pointer",
            "button-unknown",
            "button-inject",
            "button-capability",
            "scroll-inject",
            "scroll-unit",
        }
        for finding in findings:
            if finding.blocking and finding.code not in hard:
                remapped.append(
                    CompatibilityFinding(
                        code=finding.code,
                        message=finding.message + " (permissive: continuing)",
                        blocking=False,
                    )
                )
            else:
                remapped.append(finding)
        findings = remapped

    ok = not any(f.blocking for f in findings)
    return CompatibilityReport(
        ok=ok,
        findings=tuple(findings),
        source_layout=source_layout,
        target_layout=target_layout,
        source_capabilities=source_caps,
        target_capabilities=target_capabilities,
        mapping=active_mapping,
        metadata_sufficient=metadata_sufficient,
    )
