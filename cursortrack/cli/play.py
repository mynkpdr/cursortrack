"""Play subcommand executing cursor events on the real display."""

from __future__ import annotations

import contextlib
import sys
import time
from typing import Any, Callable

import typer
from rich.console import Console
from rich.progress import Progress

from cursortrack.backends import get_backend
from cursortrack.core.events import ButtonEvent, ScrollEvent, TapEvent
from cursortrack.core.session import Session

app = typer.Typer(
    help="Play back a recorded input session. Abort by moving the mouse to a corner or pressing Esc."
)
console = Console()


def _is_in_corner(x: int, y: int, ox: int, oy: int, w: int, h: int) -> bool:
    """Check if (x, y) is within the fail-safe tolerance of a screen-bounds corner.

    Args:
        x: X coordinate to test.
        y: Y coordinate to test.
        ox: Virtual screen origin X (can be negative on multi-monitor setups
            where a secondary monitor sits left of the primary one).
        oy: Virtual screen origin Y (can be negative if a monitor sits above
            the primary one).
        w: Virtual screen width.
        h: Virtual screen height.
    """
    near_left = x <= ox + 5
    near_right = x >= ox + w - 6
    near_top = y <= oy + 5
    near_bottom = y >= oy + h - 6
    return (near_left or near_right) and (near_top or near_bottom)


def precise_wait(
    target: float, perf: Callable[[], float], spin: bool, spin_threshold: float = 0.0012
) -> None:
    """Precisely wait until target time using sleep and optional busy-wait spinning."""
    if not spin:
        r = target - perf()
        if r > 0:
            time.sleep(r)
        return
    while True:
        r = target - perf()
        if r <= 0:
            return
        if r > spin_threshold:
            time.sleep(r - spin_threshold)


@app.command()
def play(
    file: str = typer.Argument(..., help="Path to the recording file (.ctrk, .npy, or .jsonl)."),
    speed: float = typer.Option(
        1.0, "--speed", "-s", help="Playback speed multiplier (e.g. 2.0 = double speed)."
    ),
    delay: int = typer.Option(
        3, "--delay", "-d", help="Countdown delay in seconds before playback starts."
    ),
    loop: bool = typer.Option(
        False, "--loop", "-l", help="Loop playback continuously until interrupted."
    ),
    backend_name: str = typer.Option(
        "auto", "--backend", "-b", help="Backend driver: auto, win, linux, macos."
    ),
    spin: bool = typer.Option(
        True,
        "--spin/--no-spin",
        help="Busy-wait the last ~1.2ms for high-precision timing (higher CPU usage).",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress status and progress displays."
    ),
) -> None:
    """Drive the physical cursor using events recorded in a session file."""
    if speed <= 0:
        console.print("[bold red]Error:[/bold red] Playback --speed must be greater than 0.")
        raise typer.Exit(code=1)

    try:
        session = Session.load(file)
    except Exception as e:
        console.print(f"[bold red]Error loading file:[/bold red] {e}")
        raise typer.Exit(code=1)

    if not session.events:
        console.print("[yellow]Recording contains no events. Nothing to play.[/yellow]")
        return

    # Initialize backend
    try:
        backend = get_backend(backend_name)
    except Exception as e:
        console.print(f"[bold red]Error initializing backend:[/bold red] {e}")
        raise typer.Exit(code=1)

    # Detect virtual screen bounds (origin can be negative on multi-monitor
    # setups where a secondary monitor sits left of or above the primary one)
    scr_ox, scr_oy, scr_w, scr_h = backend.get_screen_bounds()

    # Countdown delay. Sleep unconditionally so -q still enforces the safety
    # grace period; only the per-second prints are gated on quiet. No Esc
    # listener is running yet, so a Ctrl-C here needs no listener cleanup.
    if not quiet:
        console.print(
            f"Preparing to play '[cyan]{file}[/cyan]' ({len(session.events)} events) at {speed}x speed..."
        )
        console.print(
            "[bold yellow]FAIL-SAFE: Move mouse to any corner or press Esc to abort playback immediately.[/bold yellow]"
        )
    if delay > 0:
        try:
            for sec in range(delay, 0, -1):
                if not quiet:
                    console.print(f"Starting in {sec}... (Ctrl+C to abort)")
                time.sleep(1)
        except KeyboardInterrupt:
            if not quiet:
                console.print("\n[yellow]Countdown aborted by user (Ctrl+C).[/yellow]")
            raise typer.Exit(code=130)

    abort_keyboard = False
    kb_listener: Any = None
    try:
        from pynput import keyboard

        def on_press(key: Any) -> None:
            nonlocal abort_keyboard
            if key == keyboard.Key.esc:
                abort_keyboard = True

        kb_listener = keyboard.Listener(on_press=on_press)
        kb_listener.start()
    except ImportError:
        if not quiet:
            console.print(
                "[yellow]Warning: 'pynput' not installed. Keyboard abort shortcut (Esc) is disabled.[/yellow]"
            )
    except Exception as e:
        # pynput is installed but couldn't hook the keyboard (no display server,
        # missing permissions, platform quirk, etc). The Esc shortcut is a convenience
        # on top of the mouse-to-corner fail-safe, not the only abort path, so degrade
        # gracefully instead of crashing playback over it.
        kb_listener = None
        if not quiet:
            console.print(
                f"[yellow]Warning: Could not start keyboard listener ({e}). "
                "Keyboard abort shortcut (Esc) is disabled; the mouse-to-corner "
                "fail-safe still works.[/yellow]"
            )

    # Windows timer resolution adjustment
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

    perf = time.perf_counter

    def run_playback_once() -> bool:
        events = session.events
        origin = perf()
        n = len(events)

        last_expected_x: int | None = None
        last_expected_y: int | None = None

        with Progress(disable=quiet, transient=True) as progress:
            task = progress.add_task("[green]Playing...", total=n)

            for i, ev in enumerate(events):
                # Timing coordination
                target = origin + (ev.frame / session.rate) / speed
                precise_wait(target, perf, spin)

                # Check keyboard abort
                if abort_keyboard:
                    console.print(
                        "\n[bold red]Abort keyboard shortcut (Esc) pressed![/bold red] Aborting playback."
                    )
                    return False

                # Check fail-safe: did user force mouse to a corner?
                try:
                    rx, ry = backend.read_position()
                    is_in_corner = _is_in_corner(rx, ry, scr_ox, scr_oy, scr_w, scr_h)
                    last_expected_in_corner = False
                    if last_expected_x is not None and last_expected_y is not None:
                        last_expected_in_corner = _is_in_corner(
                            last_expected_x, last_expected_y, scr_ox, scr_oy, scr_w, scr_h
                        )
                    if is_in_corner and not last_expected_in_corner:
                        console.print(
                            f"\n[bold red]Fail-safe triggered at cursor ({rx}, {ry})![/bold red] Aborting playback."
                        )
                        return False
                except Exception:
                    pass

                # Emulate movement
                backend.set_position(ev.x, ev.y)
                last_expected_x = ev.x
                last_expected_y = ev.y

                # Emulate clicks & scrolls
                if isinstance(ev, ButtonEvent):
                    backend.click(ev.button, ev.pressed)
                elif isinstance(ev, ScrollEvent):
                    backend.scroll(ev.sdx, ev.sdy)
                elif isinstance(ev, TapEvent):
                    backend.click("left", True)
                    backend.click("left", False)

                progress.update(task, completed=i + 1)
        return True

    aborted = False
    interrupted = False
    try:
        while True:
            success = run_playback_once()
            if not success:
                aborted = True
                break
            if not loop:
                break
            if not quiet:
                console.print("Replaying loop...")
                time.sleep(0.5)
    except KeyboardInterrupt:
        aborted = True
        interrupted = True
        if not quiet:
            console.print("\n[yellow]Playback stopped by user (Ctrl+C).[/yellow]")
    finally:
        if kb_listener is not None:
            with contextlib.suppress(Exception):
                kb_listener.stop()
        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

    if not quiet and not aborted:
        console.print("[bold green]✔ Playback complete.[/bold green]")

    # An aborted playback must not exit 0 like success: scripts checking $?
    # need to tell fail-safe/Esc aborts (1) apart from a Ctrl-C signal (130).
    if interrupted:
        raise typer.Exit(code=130)
    if aborted:
        raise typer.Exit(code=1)
