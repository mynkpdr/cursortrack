"""Shared filesystem-safety helpers used by multiple CLI subcommands."""

from __future__ import annotations

import os

import typer
from rich.console import Console


def refuse_overwrite(path: str, force: bool, console: Console) -> None:
    """Abort with exit code 1 if `path` already exists and `force` is not set."""
    if os.path.exists(path) and not force:
        console.print(
            f"[bold red]Error:[/bold red] Refusing to overwrite existing file: {path}. "
            "Use --force to overwrite it anyway."
        )
        raise typer.Exit(code=1)
