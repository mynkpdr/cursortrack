"""Record subcommand capturing input events to a binary session file."""

from __future__ import annotations

import contextlib
import math
import os
import queue
import signal
import sys
import time
from typing import Any, Callable, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from cursortrack.backends import get_backend
from cursortrack.cli._format import format_hms, format_size
from cursortrack.cli._io import AtomicOutput, refuse_overwrite
from cursortrack.core.codec import (
    CODEC_NAME,
    CODEC_RAW,
    CODEC_ZLIB,
    CODEC_ZSTD,
    CodecWriter,
)
from cursortrack.core.events import (
    BUTTON_ID,
    CAP_CLICK,
    CAP_MOUSE,
    CAP_MOVE,
    CAP_SCROLL,
    encode_click,
    encode_move,
    encode_scroll,
)
from cursortrack.core.format import pack_header

app = typer.Typer(help="Record mouse position and button clicks to a session file.")
console = Console()

_STOP = False


def _on_signal(_signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True


def resolve_codec(name: str) -> int:
    """Resolve user codec choice into integer constant, dynamically testing import availability."""
    n = name.lower()
    if n == "raw":
        return CODEC_RAW
    if n == "zlib":
        return CODEC_ZLIB
    if n == "zstd":
        try:
            import zstandard

            return CODEC_ZSTD
        except ImportError:
            raise typer.BadParameter(
                "zstd compression requested but 'zstandard' library is not installed. Use 'zlib' or 'raw'."
            )
    # auto
    try:
        import zstandard  # noqa: F401

        return CODEC_ZSTD
    except ImportError:
        return CODEC_ZLIB


def parse_capture_arg(s: str) -> int:
    """Parse comma-separated capture target flags into bitmask value."""
    val = s.strip().lower()
    if val == "all":
        return CAP_MOUSE
    mask = 0
    name_to_bit = {
        "move": CAP_MOVE,
        "click": CAP_CLICK,
        "scroll": CAP_SCROLL,
    }
    for p in val.split(","):
        part = p.strip()
        if not part:
            continue
        if part == "touch":
            raise typer.BadParameter(
                "Touch capture is not supported by the current backends. "
                "Use move, click, scroll, or all."
            )
        if part not in name_to_bit:
            raise typer.BadParameter(
                f"Unknown capture flag '{part}'. Valid options: move, click, scroll, all."
            )
        mask |= name_to_bit[part]

    if mask == 0:
        raise typer.BadParameter("Capture target flags cannot be empty.")
    if not (mask & CAP_MOVE):
        raise typer.BadParameter("Capture flags must include 'move' to maintain tick timings.")
    return mask


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


def make_status_panel(
    duration_str: str,
    frames: int,
    clicks: int,
    scrolls: int,
    size_str: str,
    filepath: str,
    backend: str,
    codec: str,
    hz: int,
) -> Panel:
    """Build a rich live monitoring Panel."""
    text = Text()
    text.append("Recording to:  ", style="bold white")
    text.append(f"{filepath}\n", style="cyan")
    text.append("Backend:        ", style="bold white")
    text.append(f"{backend}   ", style="green")
    text.append("Codec: ", style="bold white")
    text.append(f"{codec}   ", style="green")
    text.append("Rate: ", style="bold white")
    text.append(f"{hz} Hz\n\n", style="green")

    text.append("Duration:      ", style="bold white")
    text.append(f"{duration_str}\n", style="bold yellow")
    text.append("Move Frames:   ", style="bold white")
    text.append(f"{frames}\n", style="cyan")
    text.append("Button Clicks: ", style="bold white")
    text.append(f"{clicks}\n", style="cyan")
    text.append("Scrolls:       ", style="bold white")
    text.append(f"{scrolls}\n\n", style="cyan")

    text.append("On-disk Size:  ", style="bold white")
    text.append(f"{size_str}\n\n", style="magenta")

    text.append("Press ", style="dim")
    text.append("Ctrl+C", style="bold red")
    text.append(" to stop recording and safely finalize.", style="dim")

    return Panel(
        text,
        title="[bold red]🔴 CursorTrack Recorder[/bold red]",
        border_style="red",
        expand=False,
    )


@app.command()
def record(
    out: Optional[str] = typer.Option(
        None, "--out", "-o", help="Output path (defaults to a timestamped file name)."
    ),
    capture_flags: str = typer.Option(
        "move",
        "--capture",
        "-c",
        help="What to record: comma-separated list of move, click, scroll, or all.",
    ),
    hz: int = typer.Option(
        144, "--hz", help="Move sampling frequency in Hertz (samples per second)."
    ),
    hours: float = typer.Option(0.0, "--hours", help="Hours limit for recording duration."),
    minutes: float = typer.Option(0.0, "--minutes", help="Minutes limit for recording duration."),
    seconds: float = typer.Option(0.0, "--seconds", help="Seconds limit for recording duration."),
    codec_name: str = typer.Option(
        "auto", "--codec", help="Compression codec: auto, zstd, zlib, raw."
    ),
    level: Optional[int] = typer.Option(
        None,
        "--level",
        help=(
            "Compression strength level. Auto-selected per codec if omitted "
            "(zlib: 6, zstd: 19). Valid ranges: zlib 0-9, zstd 1-22."
        ),
    ),
    flush_secs: float = typer.Option(
        1.0,
        "--flush-secs",
        help="Write buffer flushing frequency in seconds (limits data lost on crash).",
    ),
    backend_name: str = typer.Option(
        "auto", "--backend", "-b", help="Backend driver: auto, win, linux, macos."
    ),
    spin: bool = typer.Option(
        True,
        "--spin/--no-spin",
        help="Busy-wait the last ~1.2ms for high-precision polling intervals.",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the live dashboard display."),
    delay: int = typer.Option(
        3, "--delay", "-d", help="Countdown delay in seconds before recording starts."
    ),
    force: bool = typer.Option(
        False,
        "--force/--no-force",
        "-f",
        help="Overwrite the output file if it already exists.",
    ),
) -> None:
    """Record physical mouse input into a compact, crash-safe binary format."""
    global _STOP
    _STOP = False

    duration_values = (hours, minutes, seconds)
    if not all(math.isfinite(value) for value in duration_values):
        console.print("[bold red]Error:[/bold red] --hours/--minutes/--seconds must be finite.")
        raise typer.Exit(code=1)
    if any(value < 0 for value in duration_values):
        console.print(
            "[bold red]Error:[/bold red] --hours/--minutes/--seconds duration values "
            "cannot be negative."
        )
        raise typer.Exit(code=1)
    if not math.isfinite(flush_secs) or flush_secs <= 0:
        console.print("[bold red]Error:[/bold red] --flush-secs must be greater than 0.")
        raise typer.Exit(code=1)
    if delay < 0:
        console.print("[bold red]Error:[/bold red] --delay cannot be negative.")
        raise typer.Exit(code=1)

    capture = parse_capture_arg(capture_flags)
    codec = resolve_codec(codec_name)

    if level is None:
        level = 19 if codec == CODEC_ZSTD else 6
    elif codec == CODEC_ZSTD and not (1 <= level <= 22):
        console.print(
            f"[bold red]Error:[/bold red] --level must be 1..22 for the zstd codec (got {level})."
        )
        raise typer.Exit(code=1)
    elif codec == CODEC_ZLIB and not (0 <= level <= 9):
        console.print(
            f"[bold red]Error:[/bold red] --level must be 0..9 for the zlib codec (got {level})."
        )
        raise typer.Exit(code=1)

    if hz < 1 or hz > 65535:
        console.print("[bold red]Error:[/bold red] --hz sample rate must be 1..65535.")
        raise typer.Exit(code=1)

    period = 1.0 / hz
    duration = hours * 3600 + minutes * 60 + seconds
    total_ticks = None if duration <= 0 else round(hz * duration)

    # Resolve default output path
    if out is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_file = f"cursor_{timestamp}.ctrk"
    else:
        out_file = out

    refuse_overwrite(out_file, force, console)

    # Fetch backend
    try:
        backend = get_backend(backend_name)
    except Exception as e:
        console.print(f"[bold red]Error resolving backend:[/bold red] {e}")
        raise typer.Exit(code=1)

    try:
        scr_w, scr_h = backend.get_screen_size()
    except Exception as e:
        console.print(f"[bold red]Failed to read screen size:[/bold red] {e}")
        raise typer.Exit(code=1)

    # Wire signals
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, _on_signal)

    # Countdown delay before recording starts. Quiet mode suppresses only the
    # messages, never the requested safety delay.
    if delay > 0:
        try:
            for sec in range(delay, 0, -1):
                if _STOP:
                    raise typer.Exit(code=130)
                if not quiet:
                    console.print(f"Recording starting in {sec}... (Ctrl+C to abort)")
                time.sleep(1)
        except KeyboardInterrupt:
            raise typer.Exit(code=130) from None
        if _STOP:
            raise typer.Exit(code=130)

    start_time = time.time()
    try:
        x0, y0 = backend.read_position()
    except Exception as e:
        console.print(f"[bold red]Failed to read initial cursor position:[/bold red] {e}")
        raise typer.Exit(code=1)

    # Dynamic Windows high-res timer
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

    q: queue.Queue[tuple[str, tuple[Any, ...], float]] = queue.Queue()

    def on_event_callback(kind: str, payload: tuple[Any, ...], t_perf: float) -> None:
        q.put((kind, payload, t_perf))

    # Start listener only for event types the backends actually expose.
    needs_listener = bool(capture & (CAP_CLICK | CAP_SCROLL))
    if needs_listener:
        try:
            backend.start_listening(on_event_callback, capture)
        except Exception as e:
            console.print(f"[bold red]Failed to establish hooks for clicks/scrolls:[/bold red] {e}")
            raise typer.Exit(code=1)

    # Preserve an existing destination until a forced replacement completes.
    replacement = AtomicOutput(out_file) if force and os.path.exists(out_file) else None
    write_path = replacement.path if replacement is not None else out_file

    # Initialize file writer
    try:
        f = open(write_path, "wb")
    except Exception as e:
        if replacement is not None:
            replacement.discard()
        if needs_listener:
            backend.stop_listening()
        console.print(f"[bold red]Failed to open output file for writing:[/bold red] {e}")
        raise typer.Exit(code=1)

    f.write(pack_header(codec, hz, scr_w, scr_h, start_time, x0, y0, capture))
    try:
        writer = CodecWriter(f, codec, level)
    except Exception:
        f.close()
        if replacement is not None:
            replacement.discard()
        if needs_listener:
            backend.stop_listening()
        raise

    buf = bytearray()
    frame = 0
    last_event_frame = 0
    prev_pos = (x0, y0)
    event_counts = {"move": 1, "down": 0, "up": 0, "scroll": 0}
    unknown_buttons_seen: set[str] = set()

    perf = time.perf_counter
    record_t0 = perf()
    flush_every_ticks = max(1, round(hz * flush_secs))

    def handle_queued_events() -> None:
        nonlocal prev_pos, last_event_frame
        while True:
            try:
                kind, payload, t_perf = q.get_nowait()
            except queue.Empty:
                break

            # Buttons outside the format's vocabulary must be dropped before any
            # frame/position bookkeeping is touched: encoding them as "left"
            # (the old behavior) made replay perform clicks the user never made.
            if kind == "click" and capture & CAP_CLICK:
                btn_name = payload[2]
                if btn_name not in BUTTON_ID:
                    if btn_name not in unknown_buttons_seen:
                        unknown_buttons_seen.add(btn_name)
                        console.print(
                            f"[yellow]Warning:[/yellow] ignoring clicks from "
                            f"unsupported button '{btn_name}' (not representable "
                            f"in the session format)."
                        )
                    continue

            ev_frame = max(last_event_frame, round((t_perf - record_t0) * hz))
            dframes = max(0, ev_frame - last_event_frame)
            last_event_frame = ev_frame

            if kind == "click":
                x, y, btn_name, pressed = payload
                dx, dy = x - prev_pos[0], y - prev_pos[1]
                prev_pos = (x, y)

                if capture & CAP_CLICK:
                    # btn_name is guaranteed known here by the guard above.
                    encode_click(buf, dframes, pressed, BUTTON_ID[btn_name], dx, dy)
                    event_counts["down" if pressed else "up"] += 1

            elif kind == "scroll":
                x, y, sdx, sdy = payload
                dx, dy = x - prev_pos[0], y - prev_pos[1]
                prev_pos = (x, y)
                encode_scroll(buf, dframes, sdx, sdy, dx, dy)
                event_counts["scroll"] += 1

    next_t = perf()
    last_ui_update = perf()

    console.print(f"Recording initialized. Sample rate: {hz}Hz. Press Ctrl+C to abort.")

    recording_failed = False
    backend_error: Exception | None = None
    try:
        # Construct live view if not quiet
        live: Live | None = None
        if not quiet:
            live = Live(
                make_status_panel(
                    "00:00:00",
                    0,
                    0,
                    0,
                    "0B",
                    out_file,
                    backend_name,
                    CODEC_NAME[codec],
                    hz,
                ),
                console=console,
                refresh_per_second=2,
            )
            live.start()

        while not _STOP and (total_ticks is None or frame < total_ticks):
            next_t += period
            try:
                cur = backend.read_position()
            except Exception as e:
                backend_error = e
                recording_failed = True
                break

            if capture & CAP_MOVE:
                # last_event_frame can sit ahead of the tick counter when a
                # listener event's wall-clock frame rounded past it. Advance by
                # exactly what we encode - never rewind - so the bookkeeping
                # stays in lockstep with the decoder's accumulated frame count.
                dframes = max(1, frame + 1 - last_event_frame)
                dx = cur[0] - prev_pos[0]
                dy = cur[1] - prev_pos[1]
                encode_move(buf, dframes, dx, dy)
                event_counts["move"] += 1
                last_event_frame += dframes

            prev_pos = cur
            frame += 1

            if needs_listener:
                handle_queued_events()

            if frame % flush_every_ticks == 0:
                writer.write(bytes(buf))
                buf.clear()
                writer.flush()

                # Update live stats
                now = perf()
                if live is not None and now - last_ui_update >= 0.5:
                    last_ui_update = now
                    size_str = format_size(f.tell())
                    duration_str = format_hms(frame / hz)
                    live.update(
                        make_status_panel(
                            duration_str,
                            event_counts["move"],
                            event_counts["down"],
                            event_counts["scroll"],
                            size_str,
                            out_file,
                            backend_name,
                            CODEC_NAME[codec],
                            hz,
                        )
                    )

            precise_wait(next_t, perf, spin)

        if live is not None:
            live.stop()

    except KeyboardInterrupt:
        pass
    except BaseException:
        recording_failed = True
        raise
    finally:
        try:
            if needs_listener:
                backend.stop_listening()
                handle_queued_events()

            if buf:
                writer.write(bytes(buf))
            if recording_failed:
                # A deliberately incomplete trailing varint makes the recovered
                # prefix self-identify as truncated even when compression closes
                # cleanly (and for the raw codec, which has no frame footer).
                writer.write(b"\x80")
            writer.close()
        except BaseException:
            recording_failed = True
            raise
        finally:
            f.close()

            if sys.platform.startswith("win"):
                try:
                    import ctypes

                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass

            if replacement is not None:
                if recording_failed:
                    replacement.discard()
                else:
                    try:
                        replacement.commit()
                    except BaseException:
                        replacement.discard()
                        raise

    if backend_error is not None:
        outcome = (
            "The existing destination was preserved."
            if replacement is not None
            else f"A recoverable prefix was finalized at {out_file}."
        )
        console.print(
            f"[bold red]Recording stopped because cursor position read failed:[/bold red] "
            f"{backend_error}. {outcome}"
        )
        raise typer.Exit(code=1)

    actual_size = os.path.getsize(out_file)
    console.print(
        f"\n[bold green]✔ Recording complete.[/bold green]\n"
        f"  • Destination: {out_file}\n"
        f"  • Codec:       {CODEC_NAME[codec]}\n"
        f"  • Size:        {format_size(actual_size)}\n"
        f"  • Duration:    {format_hms(frame / hz)}\n"
        f"  • Events:      {event_counts['move']} moves, {event_counts['down']} clicks, {event_counts['scroll']} scrolls."
    )
