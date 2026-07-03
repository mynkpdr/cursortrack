"""Abstract Base Class representing an Input/Mouse Backend."""

from __future__ import annotations

import abc
from typing import Any, Callable


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
        """Read the system primary screen resolution.

        Returns:
            Tuple[width, height] in pixels, or (0, 0) if unknown.
        """
        pass

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
            capture_mask: Bitmask of CAP_MOVE, CAP_CLICK, CAP_SCROLL, CAP_TOUCH to record.
        """
        pass

    @abc.abstractmethod
    def stop_listening(self) -> None:
        """Stop the background listener thread and clean up hooks."""
        pass
