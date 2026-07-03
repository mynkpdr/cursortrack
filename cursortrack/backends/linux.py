"""Linux input tracking and emulation backend stub (planned)."""

from __future__ import annotations

from typing import Any, Callable

from cursortrack.backends.base import InputBackend


class LinuxBackend(InputBackend):
    """Linux backend stub.

    To implement Linux support:
    1. Read and write position using Xlib/X11 or python-xlib (for X11 displays)
       or portal APIs/evdev (for Wayland displays).
    2. Hook global mouse events via python-xlib, evdev, or pynput.
    3. Refer to CONTRIBUTING.md for details.
    """

    def __init__(self) -> None:
        raise NotImplementedError(
            "Linux support is planned but not yet implemented. "
            "Please check CONTRIBUTING.md for information on how to help build this backend!"
        )

    def read_position(self) -> tuple[int, int]:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")

    def set_position(self, x: int, y: int) -> None:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")

    def get_screen_size(self) -> tuple[int, int]:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")

    def click(self, button: str, pressed: bool) -> None:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")

    def scroll(self, sdx: int, sdy: int) -> None:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")

    def stop_listening(self) -> None:
        raise NotImplementedError("Linux support planned — see CONTRIBUTING.md")
