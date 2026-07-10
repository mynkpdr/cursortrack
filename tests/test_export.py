"""Tests for exporters (NumPy, JSONL, Parquet) and their round-trip reload."""

from __future__ import annotations

import csv
import json
import os

import pytest

from cursortrack.core.events import ButtonEvent, MoveEvent, ScrollEvent, TapEvent
from cursortrack.core.session import Session
from cursortrack.export import export_to_csv, export_to_jsonl, export_to_npy, export_to_parquet


def _sample_session() -> Session:
    events = [
        MoveEvent(frame=0, x=500, y=500),
        MoveEvent(frame=1, x=505, y=505),
        ButtonEvent(frame=2, x=505, y=505, button="left", pressed=True),
        ButtonEvent(frame=3, x=505, y=505, button="left", pressed=False),
        ScrollEvent(frame=4, x=505, y=505, sdx=0, sdy=-1),
        TapEvent(frame=5, x=510, y=510, touch_id=0),
    ]
    header = {
        "version": 2,
        "codec": 0,
        "rate": 100,
        "scr_w": 1920,
        "scr_h": 1080,
        "start": 1000.0,
        "x0": 500,
        "y0": 500,
        "capture": 15,
    }
    return Session(header, events)


def test_npy_export_and_reload(tmp_path: object) -> None:
    """Verify .npy export writes all events and reload preserves payload values."""
    pytest.importorskip("numpy")
    session = _sample_session()
    out_path = str(tmp_path) + "/out.npy"

    count = export_to_npy(session, out_path)
    assert count == len(session.events)
    assert os.path.exists(out_path)

    reloaded = Session.load_npy(out_path)
    assert len(reloaded.events) == len(session.events)
    assert reloaded.rate == session.rate
    assert reloaded.screen_width == session.screen_width
    assert reloaded.screen_height == session.screen_height
    assert reloaded.capture_mask == session.capture_mask

    types = [type(e).__name__ for e in reloaded.events]
    assert types == [type(e).__name__ for e in session.events]

    down_event = reloaded.events[2]
    assert isinstance(down_event, ButtonEvent)
    assert down_event.button == "left"
    assert down_event.pressed is True

    up_event = reloaded.events[3]
    assert isinstance(up_event, ButtonEvent)
    assert up_event.pressed is False

    scroll_event = reloaded.events[4]
    assert isinstance(scroll_event, ScrollEvent)
    assert scroll_event.sdy == -1

    tap_event = reloaded.events[5]
    assert isinstance(tap_event, TapEvent)
    assert tap_event.touch_id == 0
    assert tap_event.x == 510
    assert tap_event.y == 510


def test_npy_export_of_empty_session_reloads_cleanly(tmp_path: object) -> None:
    """An empty session must still export/reload as a valid 2D array, not a 1D one.

    Regression test: np.array([]) produces shape (0,) rather than (0, 6), which
    Session.load_npy previously rejected with a spurious "not 2D" error.
    """
    pytest.importorskip("numpy")
    header = {
        "version": 2,
        "codec": 0,
        "rate": 100,
        "scr_w": 1920,
        "scr_h": 1080,
        "start": 1000.0,
        "x0": 0,
        "y0": 0,
        "capture": 15,
    }
    session = Session(header, [])
    out_path = str(tmp_path) + "/empty.npy"

    count = export_to_npy(session, out_path)
    assert count == 0

    reloaded = Session.load_npy(out_path)
    assert reloaded.events == []


def test_npy_export_uses_the_exact_library_destination(tmp_path: object) -> None:
    """The exporter must not mutate a caller-owned path after safety checks."""
    pytest.importorskip("numpy")
    session = _sample_session()
    out_path = str(tmp_path) + "/exact-destination"

    count = export_to_npy(session, out_path)

    assert count == len(session.events)
    assert os.path.exists(out_path)
    assert not os.path.exists(f"{out_path}.npy")


def test_jsonl_export_and_reload(tmp_path: object) -> None:
    """Verify .jsonl export writes all events and reload preserves payload values."""
    session = _sample_session()
    out_path = str(tmp_path) + "/out.jsonl"

    count = export_to_jsonl(session, out_path)
    assert count == len(session.events)
    assert os.path.exists(out_path)

    reloaded = Session.load_jsonl(out_path)
    assert len(reloaded.events) == len(session.events)
    assert reloaded.rate == session.rate
    assert reloaded.screen_width == session.screen_width
    assert reloaded.screen_height == session.screen_height
    assert reloaded.capture_mask == session.capture_mask

    down_event = reloaded.events[2]
    assert isinstance(down_event, ButtonEvent)
    assert down_event.button == "left"
    assert down_event.pressed is True

    up_event = reloaded.events[3]
    assert isinstance(up_event, ButtonEvent)
    assert up_event.pressed is False

    scroll_event = reloaded.events[4]
    assert isinstance(scroll_event, ScrollEvent)
    assert scroll_event.sdy == -1

    tap_event = reloaded.events[5]
    assert isinstance(tap_event, TapEvent)
    assert tap_event.touch_id == 0


def test_npy_legacy_6_column_file_falls_back_to_defaults(tmp_path: object) -> None:
    """A pre-metadata .npy file (6 columns, no rate/scr_w/scr_h/capture) must still load."""
    np = pytest.importorskip("numpy")
    legacy_rows = np.array(
        [
            [1000.0, 500.0, 500.0, 0.0, 0.0, 0.0],
            [1000.01, 505.0, 505.0, 0.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    out_path = str(tmp_path) + "/legacy.npy"
    np.save(out_path, legacy_rows)

    reloaded = Session.load_npy(out_path)
    assert len(reloaded.events) == 2
    assert reloaded.rate == 144
    assert reloaded.screen_width == 0
    assert reloaded.screen_height == 0
    assert reloaded.capture_mask == 15


def test_jsonl_legacy_file_without_metadata_falls_back_to_defaults(tmp_path: object) -> None:
    """A pre-metadata .jsonl file (no rate/scr_w/scr_h/capture keys) must still load."""
    out_path = str(tmp_path) + "/legacy.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"t": 1000.0, "type": "move", "x": 500, "y": 500}) + "\n")
        f.write(json.dumps({"t": 1000.01, "type": "move", "x": 505, "y": 505}) + "\n")

    reloaded = Session.load_jsonl(out_path)
    assert len(reloaded.events) == 2
    assert reloaded.rate == 144
    assert reloaded.screen_width == 0
    assert reloaded.screen_height == 0
    assert reloaded.capture_mask == 15


def test_jsonl_metadata_uses_first_nonblank_line(tmp_path: object) -> None:
    out_path = str(tmp_path) + "/leading-blank.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n")
        f.write(
            json.dumps(
                {
                    "t": 1000.0,
                    "type": "move",
                    "x": 500,
                    "y": 500,
                    "rate": 60,
                    "scr_w": 2560,
                    "scr_h": 1440,
                    "capture": 1,
                }
            )
            + "\n"
        )
        f.write(json.dumps({"t": 1000.5, "type": "move", "x": 505, "y": 505}) + "\n")

    reloaded = Session.load_jsonl(out_path)

    assert reloaded.rate == 60
    assert reloaded.screen_width == 2560
    assert reloaded.screen_height == 1440
    assert [event.frame for event in reloaded.events] == [0, 30]


@pytest.mark.parametrize(
    "row",
    [
        {"t": 1000.0, "type": "unknown", "x": 1, "y": 2},
        {"t": 1000.0, "type": "down", "x": 1, "y": 2, "button": "button99"},
        {"t": float("nan"), "type": "move", "x": 1, "y": 2},
        {"t": 1000.0, "type": "move", "x": 1.5, "y": 2},
    ],
)
def test_jsonl_rejects_invalid_event_rows(tmp_path: object, row: dict[str, object]) -> None:
    out_path = str(tmp_path) + "/invalid.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

    with pytest.raises(ValueError, match="line 1"):
        Session.load_jsonl(out_path)


def test_jsonl_reports_malformed_json_line(tmp_path: object) -> None:
    out_path = str(tmp_path) + "/malformed.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write('{"t": 1000.0, "type": "move"\n')

    with pytest.raises(ValueError, match="line 1"):
        Session.load_jsonl(out_path)


def test_npy_rejects_nonfinite_unknown_and_inconsistent_rows(tmp_path: object) -> None:
    np = pytest.importorskip("numpy")

    nonfinite = np.array([[float("nan"), 1, 2]], dtype=np.float64)
    nonfinite_path = str(tmp_path) + "/nonfinite.npy"
    np.save(nonfinite_path, nonfinite)
    with pytest.raises(ValueError, match="finite"):
        Session.load_npy(nonfinite_path)

    unknown_type = np.array([[1000, 1, 2, 99, 0, 0]], dtype=np.float64)
    unknown_type_path = str(tmp_path) + "/unknown-type.npy"
    np.save(unknown_type_path, unknown_type)
    with pytest.raises(ValueError, match="event type"):
        Session.load_npy(unknown_type_path)

    unknown_button = np.array([[1000, 1, 2, 1, 99, 0]], dtype=np.float64)
    unknown_button_path = str(tmp_path) + "/unknown-button.npy"
    np.save(unknown_button_path, unknown_button)
    with pytest.raises(ValueError, match="button"):
        Session.load_npy(unknown_button_path)

    inconsistent = np.array(
        [
            [1000, 1, 2, 0, 0, 0, 60, 1920, 1080, 1],
            [1001, 2, 3, 0, 0, 0, 144, 1920, 1080, 1],
        ],
        dtype=np.float64,
    )
    inconsistent_path = str(tmp_path) + "/inconsistent.npy"
    np.save(inconsistent_path, inconsistent)
    with pytest.raises(ValueError, match="metadata"):
        Session.load_npy(inconsistent_path)


def test_csv_export_escapes_rows_and_neutralizes_formula_strings(tmp_path: object) -> None:
    session = _sample_session()
    dangerous_button = '=HYPERLINK("https://example.invalid"),\nnext'
    session.events = [
        ButtonEvent(
            frame=0,
            x=10,
            y=20,
            button=dangerous_button,
            pressed=True,
        )
    ]
    out_path = str(tmp_path) + "/safe.csv"

    export_to_csv(session, out_path)

    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2
    assert len(rows[1]) == 8
    assert rows[1][4] == f"'{dangerous_button}"


def test_parquet_export(tmp_path: object) -> None:
    """Verify Parquet export produces a readable table with the expected columns."""
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    import pandas as pd

    session = _sample_session()
    out_path = str(tmp_path) + "/out.parquet"

    count = export_to_parquet(session, out_path)
    assert count == len(session.events)
    assert os.path.exists(out_path)

    df = pd.read_parquet(out_path)
    assert len(df) == len(session.events)
    assert "sdy" in df.columns
    assert df.loc[4, "sdy"] == -1
