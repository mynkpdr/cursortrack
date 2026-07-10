"""Shared test configuration, fixtures, and MockBackend definition."""

from __future__ import annotations

from typing import Any, Callable

import pytest

from cursortrack.backends import BACKEND_CLASSES
from cursortrack.backends.base import InputBackend
from cursortrack.core.layout import InputCapabilities, ScrollUnit


class MockBackend(InputBackend):
    """Mock backend for headless testing environments."""

    def __init__(self) -> None:
        self.pos = (500, 500)
        self.clicks: list[tuple[str, bool]] = []
        self.scrolls: list[tuple[int, int]] = []
        self.listening = False
        self.callback: Callable[[str, tuple[Any, ...], float], None] | None = None
        self.capture_mask = 0

    def read_position(self) -> tuple[int, int]:
        return self.pos

    def set_position(self, x: int, y: int) -> None:
        self.pos = (x, y)

    def get_screen_size(self) -> tuple[int, int]:
        return (1920, 1080)

    def get_capabilities(self) -> InputCapabilities:
        layout = self.get_layout()
        return InputCapabilities(
            coordinate_unit=layout.coordinate_unit,
            coordinate_unit_id=layout.coordinate_unit_id,
            buttons=("left", "right", "middle", "x1", "x2"),
            scroll_units=(ScrollUnit.WHEEL_DETENT,),
            precise_scroll=False,
            read_position=True,
            inject_position=True,
            inject_buttons=True,
            inject_scroll=True,
            capture_buttons=True,
            capture_scroll=True,
        )

    def click(self, button: str, pressed: bool) -> None:
        self.clicks.append((button, pressed))

    def scroll(self, sdx: int, sdy: int) -> None:
        self.scrolls.append((sdx, sdy))

    def start_listening(
        self,
        on_event: Callable[[str, tuple[Any, ...], float], None],
        capture_mask: int,
    ) -> None:
        self.listening = True
        self.callback = on_event
        self.capture_mask = capture_mask

    def stop_listening(self) -> None:
        self.listening = False
        self.callback = None


@pytest.fixture(autouse=True)
def register_mock_backend() -> None:
    """Ensure the MockBackend is registered globally for all test runs."""
    BACKEND_CLASSES["mock"] = MockBackend
