"""Devices subcommand listing system-available input backends."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from cursortrack.backends import BACKEND_CLASSES, get_backend, resolve_backend_name

app = typer.Typer(help="List detected input backends and display current metrics.")
console = Console()


@app.callback(invoke_without_command=True)
def main() -> None:
    """Detect available input drivers and query screen dimensions from the active backend."""
    console.print("\n[bold blue]🖥 Input Backends & Devices[/bold blue]\n")

    # Resolve active backend
    try:
        active_name = resolve_backend_name("auto")
        active_backend = get_backend("auto")
        scr_w, scr_h = active_backend.get_screen_size()
        screen_size_str = f"{scr_w}x{scr_h}"
    except Exception as e:
        active_name = "None"
        screen_size_str = f"Error querying: {e}"

    table = Table(title="Driver Status", show_header=True)
    table.add_column("Backend Key", style="cyan")
    table.add_column("Operating System Target", style="magenta")
    table.add_column("Status", style="white")

    for key, val in BACKEND_CLASSES.items():
        is_active = key == active_name
        status_str = "[bold green]Active[/bold green]" if is_active else "[dim]Inactive[/dim]"
        if (key == "linux" and active_name == "linux") or (
            key == "macos" and active_name == "macos"
        ):
            status_str = "[bold yellow]Active (NotImplemented Stub)[/bold yellow]"

        table.add_row(key, val.__doc__ or "Stub Backend", status_str)

    console.print(table)
    console.print()

    if active_name != "None":
        console.print(f"[bold]Active Monitor Resolution:[/bold] {screen_size_str}")
    console.print()
