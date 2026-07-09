"""Shared helper for verifying that a pynput hook listener actually started.

pynput surfaces most hook-install failures (no display, a low-level hook
rejected by the OS, insufficient permissions) not by raising from
`Listener.start()`, but by leaving the listener thread dead: `running` simply
never becomes True. Trusting `start()` alone lets a recording continue with
zero clicks/scrolls and no diagnostic (see #14).
"""

from __future__ import annotations

import time
from typing import Any

#: How long to wait for pynput's listener thread to report itself running
#: before treating the hook install as failed.
STARTUP_TIMEOUT = 0.5


def verify_listener_running(
    listener: Any, error_message: str, timeout: float = STARTUP_TIMEOUT
) -> None:
    """Poll `listener.running` for up to `timeout` seconds; raise if it never comes up.

    Args:
        listener: A started pynput `Listener` (or compatible object exposing
            a `running` attribute).
        error_message: Message to raise as `RuntimeError` if the hook never
            comes up.
        timeout: Max seconds to poll before giving up.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not listener.running:
        time.sleep(0.01)
    if not listener.running:
        raise RuntimeError(error_message)
