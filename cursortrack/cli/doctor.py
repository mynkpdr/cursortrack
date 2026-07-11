"""Doctor subcommand checking system and dependency health status."""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import time
from typing import TYPE_CHECKING, Optional

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from cursortrack.backends._windows_touchpad import TouchpadProbe

app = typer.Typer(help="Verify system environment, dependencies, and permissions.")
console = Console()


def check_dependency(name: str) -> tuple[str, bool]:
    """Check if a package dependency is installed and return its version/status."""
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "Installed")
        return f"[green]Available (v{version})[/green]", True
    except ImportError:
        return "[yellow]Missing[/yellow]", False


@app.callback(invoke_without_command=True)
def main(
    touchpad_test: Optional[int] = typer.Option(
        None,
        "--touchpad-test",
        min=1,
        max=60,
        metavar="SECONDS",
        help="On Windows, print reconstructed Precision Touchpad scrolls for this many seconds.",
    ),
) -> None:
    """Run an environment/dependency health check and output diagnostic state."""
    console.print("\n[bold blue]🩺 CursorTrack System Diagnostics[/bold blue]\n")
    health_errors: list[str] = []

    # 1. OS & Python Check
    os_name = sys.platform
    python_ver = sys.version.split()[0]

    if os_name.startswith("win"):
        os_status = "[green]Supported (Windows)[/green]"
    elif os_name.startswith("linux"):
        if os.environ.get("DISPLAY"):
            os_status = "[green]Supported (Linux, X11 display detected)[/green]"
        else:
            os_status = (
                "[yellow]Supported (Linux), but no X11 display detected — "
                "set DISPLAY or run under xvfb-run[/yellow]"
            )
    else:
        os_status = f"[yellow]Partial Support ({os_name})[/yellow]"

    # 2. Build Diagnosis Table
    table = Table(title="Environment & Optional Dependencies", show_header=True)
    table.add_column("Component / Package", style="cyan")
    table.add_column("Target Status", style="magenta")
    table.add_column("Current State", style="white")

    table.add_row("Python Version", ">= 3.9", f"[green]{python_ver}[/green]")
    table.add_row("Operating System", "Windows / Linux (v0.2)", os_status)

    # Core Dependencies
    table.add_row("typer", "Required (CLI)", "[green]Available[/green]")
    table.add_row("rich", "Required (UI)", "[green]Available[/green]")

    # Optional Dependencies
    pynput_status, pynput_ok = check_dependency("pynput")
    zstd_status, _ = check_dependency("zstandard")
    numpy_status, _ = check_dependency("numpy")
    pandas_status, _ = check_dependency("pandas")
    pyarrow_status, _ = check_dependency("pyarrow")

    table.add_row("pynput", "Optional (click/scroll capture)", pynput_status)
    table.add_row("zstandard", "Optional (zstd compression)", zstd_status)
    table.add_row("numpy", "Optional (.npy export/playback)", numpy_status)
    table.add_row("pandas", "Optional (DataFrame features)", pandas_status)
    table.add_row("pyarrow", "Optional (Parquet export)", pyarrow_status)

    touchpad_probe = None
    if os_name.startswith("win"):
        from cursortrack.backends._windows_touchpad import (
            probe_precision_touchpad,
            windows_touchpad_capture_enabled,
            windows_touchpad_step_fraction,
        )

        touchpad_probe = probe_precision_touchpad()
        configuration_errors: list[str] = []
        try:
            capture_enabled = windows_touchpad_capture_enabled(default=True)
        except ValueError as error:
            capture_enabled = False
            configuration_errors.append(str(error))
        try:
            windows_touchpad_step_fraction()
        except ValueError as error:
            configuration_errors.append(str(error))
        configuration_error = "; ".join(configuration_errors) or None
        health_errors.extend(configuration_errors)

        direction = "reversed/natural" if touchpad_probe.reverse_direction else "default"
        if configuration_error is not None:
            touchpad_status = f"[red]Invalid configuration: {configuration_error}[/red]"
        elif touchpad_probe.error is not None:
            touchpad_status = f"[red]Probe failed: {touchpad_probe.error}[/red]"
            health_errors.append(touchpad_probe.error)
        elif touchpad_probe.device_count == 0:
            touchpad_status = "[yellow]No standardized Precision Touchpad detected[/yellow]"
        elif touchpad_probe.compatible_device_count == 0:
            touchpad_status = (
                f"[yellow]Detected ({touchpad_probe.device_count}), but its HID descriptor "
                "is not compatible with raw scroll reconstruction[/yellow]"
            )
        elif not touchpad_probe.pan_enabled:
            touchpad_status = (
                f"[yellow]Compatible ({touchpad_probe.compatible_device_count}); "
                "Windows two-finger panning is disabled[/yellow]"
            )
        elif not capture_enabled:
            touchpad_status = (
                f"[yellow]Compatible ({touchpad_probe.compatible_device_count}); disabled by "
                f"CURSORTRACK_WINDOWS_TOUCHPAD, {direction} direction[/yellow]"
            )
        else:
            touchpad_status = (
                f"[green]Compatible ({touchpad_probe.compatible_device_count}/"
                f"{touchpad_probe.device_count}); raw scroll capture available, "
                f"{direction} direction[/green]"
            )
        table.add_row(
            "Precision Touchpad",
            "Optional Windows raw scroll capture",
            touchpad_status,
        )

    console.print(table)
    console.print()

    if touchpad_test is not None:
        if touchpad_probe is None:
            console.print("[bold red]Touchpad test is available only on Windows.[/bold red]")
            raise typer.Exit(code=1)
        if health_errors:
            _print_health_errors(health_errors)
            raise typer.Exit(code=1)
        _run_touchpad_test(touchpad_test, touchpad_probe)
        return

    # 3. Actions / Troubleshooting Suggestions
    suggestions: dict[str, str] = {}
    if not pynput_ok:
        extra = "linux" if os_name.startswith("linux") else "windows"
        suggestions["pynput"] = (
            f"pip install cursortrack[{extra}] (needed for click/scroll capture)"
        )
    if touchpad_probe is not None and touchpad_probe.compatibility_errors:
        suggestions["touchpad"] = (
            "Check for touchpad/firmware driver updates; the detected HID "
            "descriptor lacks fields required for raw reconstruction."
        )

    if suggestions:
        console.print("[bold yellow]Suggestions for complete features:[/bold yellow]")
        for _k, v in suggestions.items():
            console.print(f"  • {v}")
        console.print()
    if health_errors:
        _print_health_errors(health_errors)
        raise typer.Exit(code=1)
    console.print("[bold green]✔ Environment health check passed.[/bold green]\n")


def _run_touchpad_test(seconds: int, probe: TouchpadProbe) -> None:
    """Print reconstructed wheel steps while the user performs two-finger pans."""
    if not probe.available:
        reason = _touchpad_unavailable_reason(probe)
        console.print(f"[bold red]Precision Touchpad test unavailable:[/bold red] {reason}")
        raise typer.Exit(code=1)

    from cursortrack.backends._windows_touchpad import PrecisionTouchpadScrollListener

    event_count = 0

    def on_scroll(sdx: int, sdy: int, _timestamp: float) -> None:
        nonlocal event_count
        event_count += 1
        console.print(f"  raw touchpad scroll: horizontal={sdx:+d} vertical={sdy:+d}")

    listener = None
    interrupted = False
    try:
        listener = PrecisionTouchpadScrollListener(on_scroll)
        started = listener.start()
    except Exception as error:
        console.print(f"[bold red]Precision Touchpad listener could not start:[/bold red] {error}")
        if listener is not None:
            with contextlib.suppress(Exception):
                listener.stop()
        raise typer.Exit(code=1) from None
    if not started:
        console.print("[bold red]Precision Touchpad listener did not start.[/bold red]")
        with contextlib.suppress(Exception):
            listener.stop()
        raise typer.Exit(code=1)

    try:
        console.print(
            f"[bold cyan]Touchpad test running for {seconds}s.[/bold cyan] "
            "Two-finger scroll over Chrome or VS Code now."
        )
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
    except KeyboardInterrupt:
        console.print("\n[yellow]Touchpad test stopped.[/yellow]")
        interrupted = True
    finally:
        try:
            listener.stop()
        except Exception as error:
            console.print(f"[bold red]Precision Touchpad cleanup failed:[/bold red] {error}")
            raise typer.Exit(code=1) from None

    if interrupted:
        raise typer.Exit(code=130)
    if listener.runtime_error is not None:
        console.print(f"[bold red]Raw touchpad parser error:[/bold red] {listener.runtime_error}")
        raise typer.Exit(code=1)
    if event_count:
        console.print(
            f"[bold green]✔ Captured {event_count} reconstructed scroll events.[/bold green]"
        )
    else:
        console.print(
            "[bold yellow]No reconstructed scroll events were seen.[/bold yellow] "
            "The device was detected, but its reports may need device-specific adjustment."
        )
        raise typer.Exit(code=1)


def _touchpad_unavailable_reason(probe: TouchpadProbe) -> str:
    if probe.error is not None:
        return probe.error
    if probe.device_count == 0:
        return "no standardized device was detected"
    if probe.compatible_device_count == 0:
        return "the detected HID descriptor is unsupported"
    if not probe.pan_enabled:
        return "Windows two-finger panning is disabled"
    return "raw capture is unavailable"


def _print_health_errors(errors: list[str]) -> None:
    console.print("[bold red]Environment health check found diagnostic errors.[/bold red]\n")
    for error in errors:
        console.print(f"  • {error}")
    console.print()
