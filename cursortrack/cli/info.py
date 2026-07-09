"""Info subcommand displaying recording statistics."""

from __future__ import annotations

import os
import time

import typer
from rich.console import Console
from rich.table import Table

from cursortrack.cli._format import format_hms, format_size, get_capture_names
from cursortrack.core.codec import CODEC_NAME
from cursortrack.core.session import Session

app = typer.Typer(help="Display header metadata and statistics for a recording file.")
console = Console()


@app.command()
def info(
    file: str = typer.Argument(
        ..., help="Path to the recording file to inspect (.ctrk, .npy, or .jsonl)."
    ),
) -> None:
    """Display recording metadata, screen bounds, start date, and event count stats."""
    if not os.path.exists(file):
        console.print(f"[bold red]Error:[/bold red] File not found: {file}")
        raise typer.Exit(code=1)

    try:
        session = Session.load(file)
    except Exception as e:
        console.print(f"[bold red]Error decoding recording file:[/bold red] {e}")
        raise typer.Exit(code=1)

    if session.truncated:
        console.print(
            "[bold yellow]Warning:[/bold yellow] recording stopped decoding early "
            "(truncated or corrupt tail) — some events may be missing."
        )

    counts: dict[str, int] = {"move": 0, "down": 0, "up": 0, "scroll": 0, "tap": 0}
    min_x = min_y = 10**9
    max_x = max_y = -(10**9)

    for ev in session.events:
        d = ev.to_dict()
        etype = str(d.get("type", "move"))
        counts[etype] = counts.get(etype, 0) + 1
        min_x, max_x = min(min_x, ev.x), max(max_x, ev.x)
        min_y, max_y = min(min_y, ev.y), max(max_y, ev.y)

    total_events = len(session.events)
    last_frame = session.events[-1].frame if session.events else 0
    duration_secs = last_frame / session.rate if session.rate else 0.0
    file_size = os.path.getsize(file)

    table = Table(title=f"Metadata Analysis: {os.path.basename(file)}", show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Format Version", str(session.version))
    table.add_row("Codec", CODEC_NAME.get(session.codec, str(session.codec)))
    table.add_row("Sample Rate", f"{session.rate} Hz")
    table.add_row(
        "Capture Bitmask",
        f"{session.capture_mask} ({', '.join(get_capture_names(session.capture_mask))})",
    )
    table.add_row("Screen Bounds", f"{session.screen_width}x{session.screen_height}")
    table.add_row(
        "Start Time",
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(session.start_time)),
    )
    table.add_row("Duration", format_hms(duration_secs))
    table.add_row("Total Event Count", str(total_events))
    table.add_row("Move Frames", str(counts.get("move", 0)))
    table.add_row(
        "Button Clicks",
        f"{counts.get('down', 0)} press / {counts.get('up', 0)} release",
    )
    table.add_row("Scroll Actions", str(counts.get("scroll", 0)))
    table.add_row("Touch Tap Events", str(counts.get("tap", 0)))

    if total_events > 0:
        table.add_row("X coordinates", f"{min_x} .. {max_x}")
        table.add_row("Y coordinates", f"{min_y} .. {max_y}")
    else:
        table.add_row("X coordinates", "-")
        table.add_row("Y coordinates", "-")

    table.add_row("On-disk File Size", format_size(file_size))

    if file_size > 0:
        # Naive: 4 bytes per event structure
        raw_approx = total_events * 4
        compression_ratio = raw_approx / file_size
        table.add_row(
            "Compression Ratio", f"~{compression_ratio:.1f}x smaller than naive uint16 packing"
        )

    console.print(table)
    console.print()
