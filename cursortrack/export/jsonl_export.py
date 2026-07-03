"""JSON Lines exporter for cursortrack Session."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cursortrack.core.session import Session


def export_to_jsonl(session: Session, out_path: str) -> int:
    """Export tracking session events into a JSON Lines (.jsonl) file.

    Each row repeats the session's rate/screen/capture metadata (constant across
    all rows) so that Session.load_jsonl() can reconstruct an accurate header
    instead of guessing, even when re-loading a jsonl file in isolation.

    Returns:
        The total number of rows written.
    """
    with open(out_path, "w", encoding="utf-8") as o:
        count = 0
        for ev in session.events:
            t = session.start_time + ev.frame / session.rate
            row = ev.to_dict()
            row["t"] = t
            row["rate"] = session.rate
            row["scr_w"] = session.screen_width
            row["scr_h"] = session.screen_height
            row["capture"] = session.capture_mask
            o.write(json.dumps(row) + "\n")
            count += 1
    return count
