"""Doctor subcommand checking system and dependency health status."""

from __future__ import annotations

import importlib
import os
import sys

import typer
from rich.console import Console
from rich.table import Table

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


def _ax_is_process_trusted() -> bool:
    """Probe AXIsProcessTrusted() directly, independent of backend construction.

    Constructing MacOSBackend already prints a stderr warning when this is
    false; `doctor` needs the raw boolean to render its own status line
    instead, so it calls the same framework function directly.
    """
    import ctypes

    try:
        aps = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        aps.AXIsProcessTrusted.restype = ctypes.c_bool
        aps.AXIsProcessTrusted.argtypes = []
        return bool(aps.AXIsProcessTrusted())
    except OSError:
        return False


@app.callback(invoke_without_command=True)
def main() -> None:
    """Run an environment/dependency health check and output diagnostic state."""
    console.print("\n[bold blue]🩺 CursorTrack System Diagnostics[/bold blue]\n")

    # 1. OS & Python Check
    os_name = sys.platform
    python_ver = sys.version.split()[0]

    ax_trusted: bool | None = None
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
    elif os_name == "darwin":
        ax_trusted = _ax_is_process_trusted()
        if ax_trusted:
            os_status = "[green]Supported (macOS, Accessibility permission granted)[/green]"
        else:
            os_status = (
                "[yellow]Supported (macOS), but Accessibility permission is missing — "
                "cursor emulation and click/scroll capture will silently do nothing. "
                "Grant it under System Settings → Privacy & Security → "
                "Accessibility[/yellow]"
            )
    else:
        os_status = f"[yellow]Partial Support ({os_name})[/yellow]"

    # 2. Build Diagnosis Table
    table = Table(title="Environment & Optional Dependencies", show_header=True)
    table.add_column("Component / Package", style="cyan")
    table.add_column("Target Status", style="magenta")
    table.add_column("Current State", style="white")

    table.add_row("Python Version", ">= 3.9", f"[green]{python_ver}[/green]")
    table.add_row("Operating System", "Windows / Linux / macOS", os_status)

    if ax_trusted is not None:
        ax_status = "[green]Granted[/green]" if ax_trusted else "[yellow]Not granted[/yellow]"
        table.add_row("Accessibility permission", "Required (emulation + capture)", ax_status)

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

    console.print(table)
    console.print()

    # 3. Actions / Troubleshooting Suggestions
    suggestions: dict[str, str] = {}
    if not pynput_ok:
        if os_name.startswith("linux"):
            extra = "linux"
        elif os_name == "darwin":
            extra = "macos"
        else:
            extra = "windows"
        suggestions["pynput"] = (
            f"pip install cursortrack[{extra}] (needed for click/scroll capture)"
        )
    if ax_trusted is False:
        suggestions["accessibility"] = (
            "Grant Accessibility permission under System Settings → Privacy & "
            "Security → Accessibility (needed for cursor emulation and click/scroll capture)"
        )

    if suggestions:
        console.print("[bold yellow]Suggestions for complete features:[/bold yellow]")
        for _k, v in suggestions.items():
            console.print(f"  • {v}")
        console.print()
    else:
        console.print(
            "[bold green]✔ Environment health check passed! All features active.[/bold green]\n"
        )
