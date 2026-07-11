"""Play subcommand executing cursor events on the real display."""

from __future__ import annotations

import contextlib
import math
import sys
import time
from typing import Any, Callable, Optional

import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from cursortrack.backends import get_backend
from cursortrack.backends._pynput_listener import verify_listener_running
from cursortrack.core.events import ButtonEvent, ScrollEvent, TapEvent
from cursortrack.core.playback import (
    CompatibilityReport,
    MappingMode,
    PlaybackMapping,
    assess_playback,
    map_point,
)
from cursortrack.core.session import Session

app = typer.Typer(
    help="Play back a recorded input session. Abort by moving the mouse to a corner or pressing Esc."
)
console = Console()

_MAPPING_CHOICES = {
    "absolute": MappingMode.ABSOLUTE,
    "scale-to-bounds": MappingMode.SCALE_TO_BOUNDS,
    "offset": MappingMode.OFFSET,
    "target-monitor": MappingMode.TARGET_MONITOR,
}
_MAX_SCROLL_SCALE = 100.0


class _ScrollTransform:
    """Scale integer scroll steps while preserving fractional remainder."""

    def __init__(self, *, scale: float, invert: bool) -> None:
        if not math.isfinite(scale) or not 0 < scale <= _MAX_SCROLL_SCALE:
            raise ValueError(
                f"scroll scale must be finite and in the range 0 < scale <= {_MAX_SCROLL_SCALE:g}"
            )
        self._factor = -scale if invert else scale
        self._remainder_x = 0.0
        self._remainder_y = 0.0

    def apply(self, sdx: int, sdy: int) -> tuple[int, int]:
        self._remainder_x += sdx * self._factor
        self._remainder_y += sdy * self._factor
        emitted_x, self._remainder_x = self._take_steps(self._remainder_x)
        emitted_y, self._remainder_y = self._take_steps(self._remainder_y)
        return emitted_x, emitted_y

    @staticmethod
    def _take_steps(value: float) -> tuple[int, float]:
        adjusted = value + math.copysign(1e-12, value)
        steps = math.trunc(adjusted)
        return steps, value - steps


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
    target: float,
    perf: Callable[[], float],
    spin: bool,
    spin_threshold: float = 0.0012,
    should_abort: Callable[[], bool] | None = None,
    poll_interval: float = 0.05,
) -> bool:
    """Wait until target time, optionally polling a cancellation callback."""
    if should_abort is None:
        if not spin:
            r = target - perf()
            if r > 0:
                time.sleep(r)
            return True
        while True:
            r = target - perf()
            if r <= 0:
                return True
            if r > spin_threshold:
                time.sleep(r - spin_threshold)

    # Long event gaps must remain interruptible. Keep the final spin window
    # precise while slicing longer sleeps so Esc/corner checks run regularly.
    while True:
        if should_abort():
            return False
        r = target - perf()
        if r <= 0:
            return True
        if spin:
            if r > spin_threshold:
                time.sleep(min(r - spin_threshold, poll_interval))
        else:
            time.sleep(min(r, poll_interval))


def _parse_mapping(
    mapping_name: str,
    offset_x: int,
    offset_y: int,
    source_monitor: Optional[str],
    target_monitor: Optional[str],
) -> PlaybackMapping:
    try:
        mode = _MAPPING_CHOICES[mapping_name]
    except KeyError as exc:
        raise typer.BadParameter(
            "Mapping must be one of: absolute, scale-to-bounds, offset, target-monitor."
        ) from exc
    try:
        return PlaybackMapping(
            mode=mode,
            offset_x=offset_x,
            offset_y=offset_y,
            source_monitor=source_monitor,
            target_monitor=target_monitor,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _print_compatibility_report(report: CompatibilityReport, *, quiet: bool) -> None:
    if quiet and report.ok:
        return

    table = Table(title="Playback compatibility", show_header=True, header_style="bold")
    table.add_column("Severity", style="bold")
    table.add_column("Code")
    table.add_column("Message")

    if not report.findings:
        table.add_row("ok", "-", "No compatibility issues detected.")
    for finding in report.findings:
        severity = "error" if finding.blocking else "warning"
        style = "red" if finding.blocking else "yellow"
        table.add_row(f"[{style}]{severity}[/{style}]", finding.code, finding.message)

    console.print(table)
    if report.mapping is not None and not quiet:
        console.print(f"Mapping mode: [cyan]{report.mapping.mode.value}[/cyan]")
    if report.source_layout is not None and report.target_layout is not None and not quiet:
        src = report.source_layout
        dst = report.target_layout
        console.print(
            f"Source layout known={src.known} unit={src.coordinate_unit.value} "
            f"bounds={src.bounds!r} monitors={src.monitor_ids}"
        )
        console.print(
            f"Target layout known={dst.known} unit={dst.coordinate_unit.value} "
            f"bounds={dst.bounds!r} monitors={dst.monitor_ids}"
        )


@app.command()
def play(
    file: str = typer.Argument(..., help="Path to the recording file (.ctrk, .npy, or .jsonl)."),
    speed: float = typer.Option(
        1.0, "--speed", "-s", help="Playback speed multiplier (e.g. 2.0 = double speed)."
    ),
    invert_scroll: bool = typer.Option(
        False,
        "--invert-scroll",
        help="Reverse both horizontal and vertical scroll directions during playback.",
    ),
    scroll_scale: float = typer.Option(
        1.0,
        "--scroll-scale",
        help="Scroll-step multiplier (e.g. 0.5 = half, 2.0 = double).",
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
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print a compatibility report and exit without injecting input.",
    ),
    strict: bool = typer.Option(
        True,
        "--strict/--permissive",
        help="Refuse incompatible layouts/capabilities (strict, default) or warn and continue.",
    ),
    mapping_name: str = typer.Option(
        "absolute",
        "--mapping",
        help="Coordinate mapping: absolute, scale-to-bounds, offset, or target-monitor.",
    ),
    offset_x: int = typer.Option(0, "--offset-x", help="X offset for --mapping offset."),
    offset_y: int = typer.Option(0, "--offset-y", help="Y offset for --mapping offset."),
    source_monitor: Optional[str] = typer.Option(
        None,
        "--source-monitor",
        help="Source monitor id for --mapping target-monitor.",
    ),
    target_monitor: Optional[str] = typer.Option(
        None,
        "--target-monitor",
        help="Target monitor id for --mapping target-monitor.",
    ),
) -> None:
    """Drive the physical cursor using events recorded in a session file."""
    if speed <= 0:
        console.print("[bold red]Error:[/bold red] Playback --speed must be greater than 0.")
        raise typer.Exit(code=1)
    try:
        _ScrollTransform(scale=scroll_scale, invert=invert_scroll)
    except ValueError as error:
        console.print(f"[bold red]Error:[/bold red] --scroll-scale {error}.")
        raise typer.Exit(code=1)

    try:
        session = Session.load(file)
    except Exception as e:
        console.print(f"[bold red]Error loading file:[/bold red] {e}")
        raise typer.Exit(code=1)

    if not session.events:
        console.print("[yellow]Recording contains no events. Nothing to play.[/yellow]")
        return

    if session.truncated and not quiet:
        console.print(
            "[bold yellow]Warning:[/bold yellow] recording stopped decoding early "
            "(truncated or corrupt tail) — playing back only the recovered events."
        )

    mapping = _parse_mapping(mapping_name, offset_x, offset_y, source_monitor, target_monitor)

    # Initialize backend
    try:
        backend = get_backend(backend_name)
    except Exception as e:
        console.print(f"[bold red]Error initializing backend:[/bold red] {e}")
        raise typer.Exit(code=1)

    try:
        target_layout = backend.get_layout()
        target_capabilities = backend.get_capabilities()
    except Exception as e:
        console.print(f"[bold red]Error reading target layout/capabilities:[/bold red] {e}")
        raise typer.Exit(code=1)

    report = assess_playback(
        session,
        target_layout,
        target_capabilities,
        mapping,
        strict=strict,
    )
    _print_compatibility_report(report, quiet=quiet and not dry_run)
    if not quiet and (invert_scroll or scroll_scale != 1.0):
        direction = "inverted" if invert_scroll else "recorded"
        console.print(f"Scroll transform: direction={direction}, scale={scroll_scale:g}")
    if not report.ok:
        console.print(
            "[bold red]Playback refused:[/bold red] resolve compatibility errors, "
            "choose an explicit --mapping, or pass --permissive after review."
        )
        raise typer.Exit(code=1)
    if dry_run:
        if not quiet:
            console.print("[bold green]✔ Dry-run complete (no input injected).[/bold green]")
        raise typer.Exit(code=0)

    # Detect virtual screen bounds (origin can be negative on multi-monitor
    # setups where a secondary monitor sits left of or above the primary one)
    try:
        scr_ox, scr_oy, scr_w, scr_h = backend.get_screen_bounds()
    except Exception as e:
        console.print(f"[bold red]Error reading screen bounds:[/bold red] {e}")
        raise typer.Exit(code=1)
    if scr_w <= 0 or scr_h <= 0:
        console.print(
            f"[bold red]Error:[/bold red] Invalid screen bounds "
            f"({scr_ox}, {scr_oy}, {scr_w}, {scr_h}); playback cannot establish "
            "a working fail-safe."
        )
        raise typer.Exit(code=1)

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

    def stop_keyboard_listener() -> None:
        if kb_listener is None:
            return
        with contextlib.suppress(Exception):
            kb_listener.stop()
        with contextlib.suppress(Exception):
            kb_listener.join(timeout=2.0)

    try:
        from pynput import keyboard

        def on_press(key: Any) -> None:
            nonlocal abort_keyboard
            if key == keyboard.Key.esc:
                abort_keyboard = True

        kb_listener = keyboard.Listener(on_press=on_press)
        kb_listener.start()
        verify_listener_running(
            kb_listener,
            "The pynput keyboard hook failed to start. Esc abort is unavailable.",
        )
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
        stop_keyboard_listener()
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
    source_layout = report.source_layout
    assert source_layout is not None

    def run_playback_once() -> bool:
        events = session.events
        origin = perf()
        n = len(events)
        scroll_transform = _ScrollTransform(scale=scroll_scale, invert=invert_scroll)

        last_expected_x: int | None = None
        last_expected_y: int | None = None
        pressed_buttons: set[str] = set()
        abort_message: str | None = None

        def should_abort() -> bool:
            nonlocal abort_message
            if abort_keyboard:
                abort_message = "Abort keyboard shortcut (Esc) pressed! Aborting playback."
                return True

            try:
                rx, ry = backend.read_position()
            except Exception as e:
                abort_message = f"Fail-safe cursor check failed ({e}); aborting playback."
                return True

            is_in_corner = _is_in_corner(rx, ry, scr_ox, scr_oy, scr_w, scr_h)
            last_expected_in_corner = False
            if last_expected_x is not None and last_expected_y is not None:
                last_expected_in_corner = _is_in_corner(
                    last_expected_x, last_expected_y, scr_ox, scr_oy, scr_w, scr_h
                )
            if is_in_corner and not last_expected_in_corner:
                abort_message = f"Fail-safe triggered at cursor ({rx}, {ry})! Aborting playback."
                return True
            return False

        def release_pressed_buttons() -> None:
            failures: list[str] = []
            for button in sorted(pressed_buttons):
                try:
                    backend.click(button, False)
                except Exception as e:
                    failures.append(f"{button}: {e}")
            pressed_buttons.clear()
            if failures:
                raise RuntimeError(
                    "Failed to release injected mouse buttons during playback cleanup: "
                    + "; ".join(failures)
                )

        try:
            with Progress(disable=quiet, transient=True) as progress:
                task = progress.add_task("[green]Playing...", total=n)

                for i, ev in enumerate(events):
                    # Timing coordination and abort checks share one loop so a
                    # sparse recording cannot suppress the fail-safe for seconds.
                    target = origin + (ev.frame / session.rate) / speed
                    if not precise_wait(target, perf, spin, should_abort=should_abort):
                        console.print(f"\n[bold red]{abort_message}[/bold red]")
                        return False

                    px, py = map_point(ev.x, ev.y, source_layout, target_layout, mapping)
                    backend.set_position(px, py)
                    last_expected_x = px
                    last_expected_y = py

                    # Emulate clicks & scrolls. Track every successful button-down
                    # so cleanup can neutralize malformed files and interrupted runs.
                    if isinstance(ev, ButtonEvent):
                        backend.click(ev.button, ev.pressed)
                        if ev.pressed:
                            pressed_buttons.add(ev.button)
                        else:
                            pressed_buttons.discard(ev.button)
                    elif isinstance(ev, ScrollEvent):
                        sdx, sdy = scroll_transform.apply(ev.sdx, ev.sdy)
                        if sdx or sdy:
                            backend.scroll(sdx, sdy)
                    elif isinstance(ev, TapEvent):
                        backend.click("left", True)
                        pressed_buttons.add("left")
                        backend.click("left", False)
                        pressed_buttons.discard("left")

                    progress.update(task, completed=i + 1)
            return True
        finally:
            release_pressed_buttons()

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
    except Exception as e:
        aborted = True
        console.print(f"\n[bold red]Playback failed:[/bold red] {e}")
    finally:
        stop_keyboard_listener()
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
