"""Export modules entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cursortrack.export.csv_export import export_to_csv
from cursortrack.export.jsonl_export import export_to_jsonl
from cursortrack.export.npy_export import export_to_npy
from cursortrack.export.parquet_export import export_to_parquet

if TYPE_CHECKING:
    from cursortrack.core.session import Session

__all__ = [
    "export_session",
    "export_to_csv",
    "export_to_jsonl",
    "export_to_npy",
    "export_to_parquet",
]


def export_session(session: Session, out_path: str, fmt: str) -> int:
    """Unified entry point to export a Session to various formats.

    Args:
        session: The cursortrack Session object.
        out_path: Destination file path.
        fmt: Format name ('csv', 'jsonl', 'npy', or 'parquet').
    """
    f = fmt.lower()
    if f == "csv":
        return export_to_csv(session, out_path)
    elif f == "jsonl":
        return export_to_jsonl(session, out_path)
    elif f == "npy":
        return export_to_npy(session, out_path)
    elif f == "parquet":
        return export_to_parquet(session, out_path)
    else:
        raise ValueError(
            f"Unsupported export format: {fmt}. "
            "Supported formats are: 'csv', 'jsonl', 'npy', 'parquet'."
        )
