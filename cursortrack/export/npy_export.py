"""NumPy NPY exporter for cursortrack Session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cursortrack.core.events import BUTTON_ID

if TYPE_CHECKING:
    from cursortrack.core.session import Session


#: Column layout written by export_to_npy() and read back by Session.load_npy().
#: Columns 6-9 (rate, scr_w, scr_h, capture) repeat the same session-level
#: constant on every row so the file is self-describing on reload; see
#: docs/file-format.md for the full interchange schema.
NPY_COLUMNS = ("t", "x", "y", "type_id", "aux1", "aux2", "rate", "scr_w", "scr_h", "capture")


def export_to_npy(session: Session, out_path: str) -> int:
    """Export tracking session events into a NumPy binary format (.npy) file.

    Requires numpy to be installed.

    Returns:
        The total number of rows written.
    """
    try:
        import numpy as np
    except ImportError:
        raise ImportError(
            "NumPy is required to export to .npy. Install it using 'pip install numpy'."
        )

    type_id_map = {"move": 0, "down": 1, "up": 2, "scroll": 3, "tap": 4}
    rows: list[tuple[float, float, float, float, float, float, float, float, float, float]] = []

    rate = float(session.rate)
    scr_w = float(session.screen_width)
    scr_h = float(session.screen_height)
    capture = float(session.capture_mask)

    for ev in session.events:
        t = session.start_time + ev.frame / session.rate
        d = ev.to_dict()
        etype = d.get("type", "move")
        tid = type_id_map.get(str(etype), 0)
        aux1 = 0.0
        aux2 = 0.0

        if etype in ("down", "up"):
            btn = str(d.get("button", "left"))
            aux1 = float(BUTTON_ID.get(btn, -1))
        elif etype == "scroll":
            aux1 = float(d.get("sdx", 0))
            aux2 = float(d.get("sdy", 0))
        elif etype == "tap":
            aux1 = float(d.get("touch_id", 0))

        rows.append(
            (t, float(ev.x), float(ev.y), float(tid), aux1, aux2, rate, scr_w, scr_h, capture)
        )

    # np.array([]) yields a 1D (0,) array rather than (0, len(NPY_COLUMNS)), which
    # Session.load_npy would then reject as not 2D. Keep the column count consistent
    # even when empty.
    arr = (
        np.array(rows, dtype=np.float64)
        if rows
        else np.empty((0, len(NPY_COLUMNS)), dtype=np.float64)
    )
    # Passing a string path makes numpy append ".npy" implicitly. Use a file
    # handle so this library API writes exactly the destination its caller
    # already validated.
    with open(out_path, "wb") as f:
        np.save(f, arr)
    return len(arr)
