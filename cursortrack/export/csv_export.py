"""CSV exporter for cursortrack Session."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cursortrack.core.session import Session


def _spreadsheet_safe(value: object) -> object:
    """Neutralize string cells that spreadsheet programs may execute as formulas."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def export_to_csv(session: Session, out_path: str) -> int:
    """Export tracking session events into a CSV file.

    Returns:
        The total number of rows written.
    """
    with open(out_path, "w", newline="", encoding="utf-8") as o:
        writer = csv.writer(o)
        writer.writerow(["t", "type", "x", "y", "button", "sdx", "sdy", "touch_id"])
        count = 0
        for ev in session.events:
            t = session.start_time + ev.frame / session.rate
            d = ev.to_dict()
            etype = d.get("type", "move")
            btn = d.get("button", "")
            sdx = d.get("sdx", "")
            sdy = d.get("sdy", "")
            touch_id = d.get("touch_id", "")

            writer.writerow(
                [
                    f"{t:.6f}",
                    _spreadsheet_safe(etype),
                    ev.x,
                    ev.y,
                    _spreadsheet_safe(btn),
                    sdx,
                    sdy,
                    touch_id,
                ]
            )
            count += 1
    return count
