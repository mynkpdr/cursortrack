"""Export subcommand translating recordings to other analytical/ML formats."""

from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console

from cursortrack.cli._io import refuse_overwrite
from cursortrack.core.session import Session
from cursortrack.export import export_session

app = typer.Typer(help="Convert cursortrack files into CSV, JSONL, NumPy, or Parquet.")
console = Console()


def _resolve_output_path(file: str, out: Optional[str], fmt: str) -> str:
    """Resolve the exact path exporters will write before safety checks run."""
    if out is None:
        base, _ = os.path.splitext(file)
        return f"{base}.{fmt}"
    if fmt == "npy" and not out.lower().endswith(".npy"):
        return f"{out}.npy"
    return out


@app.command()
def export(
    file: str = typer.Argument(
        ..., help="Path to the recording file to export (.ctrk, .npy, or .jsonl)."
    ),
    to: str = typer.Option(
        "csv",
        "--to",
        "-t",
        help="Target export format. Choices: csv, jsonl, npy, parquet.",
    ),
    out: Optional[str] = typer.Option(
        None,
        "--out",
        "-o",
        help="Optional destination path. Defaults to same directory as input file.",
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        "-f",
        help="Overwrite the destination file if it already exists.",
    ),
) -> None:
    """Translate compressed session recordings into developer/ML formats (CSV, JSONL, Npy, Parquet)."""
    if not os.path.exists(file):
        console.print(f"[bold red]Error:[/bold red] File not found: {file}")
        raise typer.Exit(code=1)

    fmt = to.lower()
    valid_formats = {"csv", "jsonl", "npy", "parquet"}
    if fmt not in valid_formats:
        console.print(
            f"[bold red]Error:[/bold red] Invalid target format '{to}'. "
            f"Supported options: {', '.join(valid_formats)}"
        )
        raise typer.Exit(code=1)

    out_path = _resolve_output_path(file, out, fmt)

    if os.path.exists(out_path) and os.path.samefile(file, out_path):
        console.print(
            f"[bold red]Error:[/bold red] Destination {out_path} is the same file as the input. "
            "Choose a different --out path."
        )
        raise typer.Exit(code=1)
    refuse_overwrite(out_path, force, console)

    console.print(f"Decoding and exporting [cyan]{file}[/cyan] -> [green]{out_path}[/green]...")

    try:
        session = Session.load(file)
        if session.truncated:
            console.print(
                "[bold yellow]Warning:[/bold yellow] recording stopped decoding early "
                "(truncated or corrupt tail) — exporting only the recovered events."
            )
        count = export_session(session, out_path, fmt)
        console.print(
            f"[bold green]✔ Export complete![/bold green] Wrote {count} events to {out_path}."
        )
    except Exception as e:
        console.print(f"[bold red]Export failed:[/bold red] {e}")
        raise typer.Exit(code=1)
