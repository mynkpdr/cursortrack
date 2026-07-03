"""Parquet exporter for cursortrack Session."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cursortrack.core.session import Session


def export_to_parquet(session: Session, out_path: str) -> int:
    """Export tracking session events into a Parquet file.

    Requires pandas and pyarrow/fastparquet to be installed.

    Returns:
        The total number of rows written.
    """
    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        raise ImportError(
            "Pandas and pyarrow are required to export to Parquet. "
            "Install them using 'pip install pandas pyarrow'."
        )

    df = session.to_dataframe()
    df.to_parquet(out_path, index=False)
    return len(df)
