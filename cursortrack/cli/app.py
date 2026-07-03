"""CLI application assembly tying together all subcommands."""

from __future__ import annotations

from typing import Optional

import typer

from cursortrack import __version__
from cursortrack.cli.devices import main as devices
from cursortrack.cli.doctor import main as doctor
from cursortrack.cli.export import export
from cursortrack.cli.info import info
from cursortrack.cli.play import play
from cursortrack.cli.record import record

app = typer.Typer(
    name="cursortrack",
    help="Professional cross-platform cursor and input tracking developer utility.",
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cursortrack version {__version__}")
        raise typer.Exit()


@app.callback()
def callback(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Print version information and exit.",
    ),
) -> None:
    """CursorTrack: Record, Replay, and Export mouse cursor tracks."""
    pass


# Register subcommands on the main app instance
app.command()(record)
app.command()(play)
app.command()(export)
app.command()(info)
app.command(name="devices")(devices)
app.command(name="doctor")(doctor)


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
