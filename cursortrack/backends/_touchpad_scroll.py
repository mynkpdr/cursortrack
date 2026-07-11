"""Platform-neutral touch-contact framing and scroll reconstruction."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

DEFAULT_STEP_FRACTION = 0.012
DEFAULT_CONTACT_TIMEOUT = 0.08
DEFAULT_DEDUPE_WINDOW = 0.08
DEFAULT_ACTIVE_WINDOW = 0.12


@dataclass(frozen=True)
class TouchContact:
    """One normalized touchpad contact currently touching the surface."""

    contact_id: int
    x: float
    y: float


@dataclass(frozen=True)
class ParsedContact:
    """Contact decoded from one HID report, including its tip-switch state."""

    contact_id: int
    x: float
    y: float
    touching: bool
    confident: bool = True


@dataclass(frozen=True)
class ParsedTouchpadReport:
    """One HID packet with its frame metadata and available contact slots."""

    scan_time: int
    contact_count: int
    contacts: tuple[ParsedContact, ...]


class TouchpadReportAssembler:
    """Build atomic Precision Touchpad frames from parallel or hybrid packets."""

    def __init__(self) -> None:
        self._scan_time: Optional[int] = None
        self._expected_contacts = 0
        self._contacts: list[ParsedContact] = []
        self.dropped_frames = 0

    def update(
        self,
        report: ParsedTouchpadReport,
    ) -> Optional[tuple[ParsedContact, ...]]:
        """Return a complete frame, or None while hybrid packets remain."""
        starts_frame = report.contact_count > 0
        same_hybrid_frame = (
            not starts_frame
            and self._scan_time is not None
            and report.scan_time == self._scan_time
            and len(self._contacts) < self._expected_contacts
        )

        if starts_frame:
            if self._scan_time is not None and len(self._contacts) < self._expected_contacts:
                self.dropped_frames += 1
            self._scan_time = report.scan_time
            self._expected_contacts = report.contact_count
            self._contacts = []
        elif not same_hybrid_frame:
            if self._scan_time is not None and len(self._contacts) < self._expected_contacts:
                self.dropped_frames += 1
            self.reset()
            return ()

        remaining = self._expected_contacts - len(self._contacts)
        if remaining > 0:
            self._contacts.extend(report.contacts[:remaining])
        if len(self._contacts) < self._expected_contacts:
            return None

        frame = tuple(self._contacts)
        self.reset()
        return frame

    def reset(self) -> None:
        self._scan_time = None
        self._expected_contacts = 0
        self._contacts = []


class ContactFrameAssembler:
    """Track intentional contacts from parsed Precision Touchpad frames."""

    def __init__(self, contact_timeout: float = DEFAULT_CONTACT_TIMEOUT) -> None:
        if not math.isfinite(contact_timeout) or contact_timeout <= 0:
            raise ValueError("contact_timeout must be finite and positive.")
        self._contact_timeout = contact_timeout
        self._live: dict[int, tuple[TouchContact, float]] = {}

    def update(
        self,
        contacts: Sequence[ParsedContact],
        timestamp: float,
    ) -> tuple[TouchContact, ...]:
        """Apply one report and return all contacts still considered live."""
        for contact in contacts:
            if contact.touching and contact.confident:
                normalized = TouchContact(
                    contact.contact_id,
                    _clamp_unit(contact.x),
                    _clamp_unit(contact.y),
                )
                self._live[contact.contact_id] = (normalized, timestamp)
            else:
                self._live.pop(contact.contact_id, None)

        stale = [
            contact_id
            for contact_id, (_, last_seen) in self._live.items()
            if timestamp - last_seen >= self._contact_timeout
        ]
        for contact_id in stale:
            del self._live[contact_id]

        return tuple(
            contact
            for contact, _ in sorted(self._live.values(), key=lambda item: item[0].contact_id)
        )

    def replace(
        self,
        contacts: Sequence[ParsedContact],
        timestamp: float,
    ) -> tuple[TouchContact, ...]:
        """Replace state from one complete Precision Touchpad frame."""
        self._live.clear()
        return self.update(contacts, timestamp)

    def reset(self) -> None:
        self._live.clear()


class TouchpadScrollTracker:
    """Infer discrete wheel steps from parallel two-finger translation."""

    def __init__(
        self,
        emit: Callable[[tuple[int, int, float]], None],
        *,
        step_fraction: float = DEFAULT_STEP_FRACTION,
        reverse_direction: bool = False,
        max_steps_per_frame: int = 8,
        active_window: float = DEFAULT_ACTIVE_WINDOW,
        on_activity: Optional[Callable[[float], None]] = None,
    ) -> None:
        if not math.isfinite(step_fraction) or step_fraction <= 0:
            raise ValueError("step_fraction must be finite and positive.")
        if max_steps_per_frame < 1:
            raise ValueError("max_steps_per_frame must be positive.")
        if not math.isfinite(active_window) or active_window <= 0:
            raise ValueError("active_window must be finite and positive.")

        self._emit = emit
        self._step_fraction = step_fraction
        self._direction = -1.0 if not reverse_direction else 1.0
        self._max_steps_per_frame = max_steps_per_frame
        self._active_window = active_window
        self._on_activity = on_activity
        self._previous: dict[int, TouchContact] = {}
        self._updated_contacts: set[int] = set()
        self._accumulated_x = 0.0
        self._accumulated_y = 0.0
        self._last_activity = -math.inf

    def feed(
        self,
        contacts: Sequence[TouchContact],
        timestamp: float,
        *,
        updated_contact_ids: Optional[Sequence[int]] = None,
    ) -> None:
        """Consume one current-contact snapshot."""
        current = {contact.contact_id: contact for contact in contacts}
        if len(current) != 2 or current.keys() != self._previous.keys():
            self._reset_motion(current)
            return
        updated = current.keys() if updated_contact_ids is None else updated_contact_ids
        self._updated_contacts.update(contact_id for contact_id in updated if contact_id in current)
        if not current.keys() <= self._updated_contacts:
            return
        self._updated_contacts.clear()

        contact_ids = tuple(sorted(current))
        first_previous = self._previous[contact_ids[0]]
        second_previous = self._previous[contact_ids[1]]
        first_current = current[contact_ids[0]]
        second_current = current[contact_ids[1]]
        first_dx = first_current.x - first_previous.x
        first_dy = first_current.y - first_previous.y
        second_dx = second_current.x - second_previous.x
        second_dy = second_current.y - second_previous.y
        self._previous = current

        translation_x = (first_dx + second_dx) / 2.0
        translation_y = (first_dy + second_dy) / 2.0
        translation = math.hypot(translation_x, translation_y)
        disagreement = math.hypot(first_dx - second_dx, first_dy - second_dy)

        # Opposing contact motion is a pinch/spread, not a two-finger pan.
        if disagreement > max(0.003, translation * 1.5):
            self._accumulated_x = 0.0
            self._accumulated_y = 0.0
            return
        if translation < 0.0003:
            return

        self._last_activity = timestamp
        if self._on_activity is not None:
            self._on_activity(timestamp)

        self._accumulated_x += translation_x * self._direction
        self._accumulated_y += translation_y * self._direction
        steps_x, self._accumulated_x = self._consume_steps(self._accumulated_x)
        steps_y, self._accumulated_y = self._consume_steps(self._accumulated_y)
        if steps_x or steps_y:
            self._emit((steps_x, steps_y, timestamp))

    def scroll_active_at(self, timestamp: float) -> bool:
        """Return whether raw two-finger translation was seen very recently."""
        return 0 <= timestamp - self._last_activity <= self._active_window

    def reset(self) -> None:
        self._reset_motion({})

    def _reset_motion(self, current: dict[int, TouchContact]) -> None:
        self._previous = current
        self._updated_contacts.clear()
        self._accumulated_x = 0.0
        self._accumulated_y = 0.0

    def _consume_steps(self, accumulated: float) -> tuple[int, float]:
        adjusted = accumulated + math.copysign(1e-12, accumulated)
        raw_steps = math.trunc(adjusted / self._step_fraction)
        steps = max(-self._max_steps_per_frame, min(self._max_steps_per_frame, raw_steps))
        # A discontinuity can imply dozens of steps. Emit the bounded burst but
        # retain only sub-step precision; carrying the excess creates phantom
        # scrolling over subsequent stationary frames.
        remainder = accumulated - raw_steps * self._step_fraction
        return steps, remainder


@dataclass(frozen=True)
class _RecentStep:
    token: int
    source: str
    axis: str
    direction: int
    timestamp: float


class ScrollEventArbiter:
    """Suppress duplicate hook/raw events while retaining unmatched input."""

    def __init__(
        self,
        emit: Callable[[str, int, int, float], None],
        *,
        dedupe_window: float = DEFAULT_DEDUPE_WINDOW,
    ) -> None:
        if not math.isfinite(dedupe_window) or dedupe_window <= 0:
            raise ValueError("dedupe_window must be finite and positive.")
        self._emit = emit
        self._dedupe_window = dedupe_window
        self._recent: list[_RecentStep] = []
        self._latest_timestamp = -math.inf
        self._next_token = 0
        self._lock = threading.Lock()
        self._dispatch_lock = threading.RLock()

    def emit_hook(self, sdx: int, sdy: int, timestamp: float) -> bool:
        return self._emit_deduplicated("hook", sdx, sdy, timestamp)

    def emit_raw(self, sdx: int, sdy: int, timestamp: float) -> bool:
        return self._emit_deduplicated("raw", sdx, sdy, timestamp)

    def _emit_deduplicated(
        self,
        source: str,
        sdx: int,
        sdy: int,
        timestamp: float,
    ) -> bool:
        # Reservation and delivery form one transaction. Serializing callers
        # prevents another source from consuming a reservation while its
        # callback is still able to fail and roll back.
        with self._dispatch_lock:
            return self._emit_serialized(source, sdx, sdy, timestamp)

    def _emit_serialized(
        self,
        source: str,
        sdx: int,
        sdy: int,
        timestamp: float,
    ) -> bool:
        with self._lock:
            self._latest_timestamp = max(self._latest_timestamp, timestamp)
            cutoff = self._latest_timestamp - self._dedupe_window
            self._recent = [event for event in self._recent if event.timestamp >= cutoff]
            emitted_x, tokens_x, consumed_x = self._reserve_axis(source, "x", sdx, timestamp)
            emitted_y, tokens_y, consumed_y = self._reserve_axis(source, "y", sdy, timestamp)
        if not emitted_x and not emitted_y:
            return False

        reserved_tokens = tokens_x | tokens_y
        try:
            self._emit(source, emitted_x, emitted_y, timestamp)
        except BaseException:
            with self._lock:
                self._recent = [
                    event for event in self._recent if event.token not in reserved_tokens
                ]
                self._recent.extend(consumed_x)
                self._recent.extend(consumed_y)
            raise
        return True

    def _reserve_axis(
        self,
        source: str,
        axis: str,
        steps: int,
        timestamp: float,
    ) -> tuple[int, set[int], list[_RecentStep]]:
        emitted = 0
        tokens: set[int] = set()
        consumed: list[_RecentStep] = []
        direction = 1 if steps > 0 else -1
        for _ in range(abs(steps)):
            candidates = [
                (abs(timestamp - event.timestamp), index)
                for index, event in enumerate(self._recent)
                if event.source != source
                and event.axis == axis
                and event.direction == direction
                and abs(timestamp - event.timestamp) <= self._dedupe_window
            ]
            if candidates:
                _, match = min(candidates)
                consumed.append(self._recent.pop(match))
                continue

            token = self._next_token
            self._next_token += 1
            self._recent.append(_RecentStep(token, source, axis, direction, timestamp))
            tokens.add(token)
            emitted += direction
        return emitted, tokens, consumed


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))
