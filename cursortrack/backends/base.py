"""Abstract Base Class representing an Input/Mouse Backend."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Callable

from cursortrack.core.layout import (
    CoordinateUnit,
    DesktopLayout,
    InputCapabilities,
    MonitorLayout,
    Rect,
)


@dataclass(frozen=True)
class EnhancedScrollCaptureStatus:
    """Observable state for an optional native scroll source."""

    requested: bool
    active: bool
    degraded_reason: str | None = None


class InputBackend(abc.ABC):
    """Abstract interface defining required inputs for mouse/cursor platforms."""

    @abc.abstractmethod
    def read_position(self) -> tuple[int, int]:
        """Read the current physical cursor position.

        Returns:
            Tuple[x, y] of absolute screen coordinates.
        """
        pass

    @abc.abstractmethod
    def set_position(self, x: int, y: int) -> None:
        """Set the physical cursor position to absolute coordinates x, y."""
        pass

    @abc.abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """Read the virtual desktop resolution spanning all monitors.

        Returns:
            Tuple[width, height] in pixels, or (0, 0) if unknown. On
            multi-monitor systems this is the bounding box of every display,
            not just the primary one; see get_screen_bounds() for its origin.
        """
        pass

    def get_screen_bounds(self) -> tuple[int, int, int, int]:
        """Read the virtual desktop's bounding box, including its origin.

        Secondary monitors placed left of or above the primary one give the
        virtual desktop a negative origin, so callers that need absolute
        corner coordinates (e.g. playback fail-safes) must use this instead
        of assuming (0, 0).

        Returns:
            Tuple[origin_x, origin_y, width, height] in pixels. The default
            implementation assumes a single monitor at the origin; backends
            spanning multiple monitors should override it.
        """
        width, height = self.get_screen_size()
        return 0, 0, width, height

    def get_layout(self) -> DesktopLayout:
        """Return known layout facts or an explicit unknown layout.

        The default synthesizes a single primary monitor from
        ``get_screen_bounds()`` with an unknown coordinate unit. Concrete
        backends should override when they can prove a unit or monitor list.
        """
        origin_x, origin_y, width, height = self.get_screen_bounds()
        if width <= 0 or height <= 0:
            return DesktopLayout.unknown()
        bounds = Rect(origin_x, origin_y, width, height)
        return DesktopLayout(
            known=True,
            coordinate_unit=CoordinateUnit.UNKNOWN,
            bounds=bounds,
            monitors=(MonitorLayout(id="primary", primary=True, bounds=bounds),),
        )

    def get_capabilities(self) -> InputCapabilities:
        """Return coordinate, button, scroll, capture, and injection semantics.

        Defaults are conservative: unknown units and no advertised injection
        until a concrete backend overrides with verified facts.
        """
        layout = self.get_layout()
        return InputCapabilities(
            coordinate_unit=layout.coordinate_unit,
            coordinate_unit_id=layout.coordinate_unit_id,
            buttons=(),
            scroll_units=(),
            precise_scroll=False,
            read_position=False,
            inject_position=False,
            inject_buttons=False,
            inject_scroll=False,
            capture_buttons=False,
            capture_scroll=False,
        )

    def request_enhanced_scroll_capture(self) -> None:
        """Opt into an available backend-specific high-fidelity scroll source.

        The base implementation records the request as unsupported. Some
        native sources claim process-wide resources, so library callers must
        opt in explicitly.
        """
        self._enhanced_scroll_requested = True

    def get_enhanced_scroll_capture_status(self) -> EnhancedScrollCaptureStatus:
        """Return whether enhanced scroll capture was requested and activated."""
        requested = getattr(self, "_enhanced_scroll_requested", False)
        return EnhancedScrollCaptureStatus(
            requested=requested,
            active=False,
            degraded_reason=(
                "This backend does not provide enhanced scroll capture." if requested else None
            ),
        )

    def check_listener_health(self) -> None:
        """Report capture degradation detectable by this backend.

        The default reports no additional health signal. Overrides must be
        idempotent, callable while listening and immediately after
        ``stop_listening()``, and preserve detected teardown failures until the
        next successful ``start_listening()`` call.
        """
        return None

    @abc.abstractmethod
    def click(self, button: str, pressed: bool) -> None:
        """Send a physical mouse button click.

        Args:
            button: 'left', 'right', 'middle', 'x1', or 'x2'.
            pressed: True for down/press, False for up/release.
        """
        pass

    @abc.abstractmethod
    def scroll(self, sdx: int, sdy: int) -> None:
        """Send a physical mouse scroll event.

        Args:
            sdx: Horizontal scroll steps.
            sdy: Vertical scroll steps.
        """
        pass

    @abc.abstractmethod
    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        """Start a background listener that records mouse interactions.

        Args:
            on_event: Callback accepting (event_type, payload_tuple, timestamp).
            capture_mask: Requested event bitmask. Backends must ignore unsupported
                capabilities; current implementations support CAP_CLICK and CAP_SCROLL
                listeners, while movement is sampled by the recorder.
        """
        pass

    @abc.abstractmethod
    def stop_listening(self) -> None:
        """Stop the background listener thread and clean up hooks."""
        pass
