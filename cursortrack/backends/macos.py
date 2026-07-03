"""macOS input tracking and emulation backend stub (planned)."""

from __future__ import annotations

from typing import Any, Callable

from cursortrack.backends.base import InputBackend


class MacOSBackend(InputBackend):
    """macOS backend stub.

    To implement macOS support:
    1. Read and write position using Quartz (via pyobjc-framework-Quartz).
    2. Hook global mouse events via Quartz event taps or pynput.
    3. Refer to CONTRIBUTING.md for details.
    """

    def __init__(self) -> None:
        raise NotImplementedError(
            "macOS support is planned but not yet implemented. "
            "Please check CONTRIBUTING.md for information on how to help build this backend!"
        )

    def read_position(self) -> tuple[int, int]:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")

    def set_position(self, x: int, y: int) -> None:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")

    def get_screen_size(self) -> tuple[int, int]:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")

    def click(self, button: str, pressed: bool) -> None:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")

    def scroll(self, sdx: int, sdy: int) -> None:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")

    def stop_listening(self) -> None:
        raise NotImplementedError("macOS support planned — see CONTRIBUTING.md")
