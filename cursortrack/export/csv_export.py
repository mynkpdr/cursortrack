"""CSV exporter for cursortrack Session."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cursortrack.core.session import Session


def export_to_csv(session: Session, out_path: str) -> int:
    """Export tracking session events into a CSV file.

    Returns:
        The total number of rows written.
    """
    with open(out_path, "w", newline="", encoding="utf-8") as o:
        o.write("t,type,x,y,button,sdx,sdy,touch_id\n")
        count = 0
        for ev in session.events:
            t = session.start_time + ev.frame / session.rate
            d = ev.to_dict()
            etype = d.get("type", "move")
            btn = d.get("button", "")
            sdx = d.get("sdx", "")
            sdy = d.get("sdy", "")
            touch_id = d.get("touch_id", "")

            o.write(
                "%.6f,%s,%d,%d,%s,%s,%s,%s\n"
                % (
                    t,
                    etype,
                    ev.x,
                    ev.y,
                    btn,
                    sdx,
                    sdy,
                    touch_id,
                )
            )
            count += 1
    return count
