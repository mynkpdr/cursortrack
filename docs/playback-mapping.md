"""Portable playback mapping and impossibility notes.

CursorTrack never silently scales, clamps, or remaps a recording. Replay across
machines is an explicit operator choice: inspect with ``play --dry-run``, then
select a mapping mode if the source and target desktops are not equivalent.

## Mapping modes

| Mode | Behavior |
|------|----------|
| `absolute` (default) | Inject recorded coordinates unchanged. Allowed when source and target bounds match; unknown units warn but do not invent a transform. |
| `scale-to-bounds` | Linearly map source desktop bounds onto target desktop bounds. Requires known layouts. Out-of-range inputs stay out of range — nothing is clamped. |
| `offset` | Add ``--offset-x`` / ``--offset-y``. Requires a nonzero offset. |
| `target-monitor` | Map a point relative to ``--source-monitor`` onto ``--target-monitor``. |

Strict mode (default) refuses incompatible known layouts, missing target
capabilities, invalid button state, and non-complete integrity. Use
``--permissive`` only after reviewing the compatibility report.

## Capability checks

Playback compares buttons, scroll units, and touch/tap presence against the
target backend's ``InputCapabilities``. Current Windows/Linux backends advertise
canonical mouse buttons and wheel-detent scroll. Tap events remain a legacy
left-click approximation and are refused in strict mode.

## Cases that remain impossible

These cannot be fixed by coordinate mapping:

- **Changed application UI** — recorded clicks land on whatever is under those
  coordinates now; layout shifts, DPI-driven UI scaling inside apps, and
  window placement are out of scope.
- **Native Wayland clients** — the Linux backend speaks X11/XWayland only.
  Global capture/injection into native Wayland surfaces requires portal or
  privileged input APIs (see ROADMAP).
- **Windows secure desktop** — UAC / login / secure attention sequences are
  not injectable from a normal user session.
- **Denied macOS permissions** — Accessibility / input-monitoring denial
  blocks capture and injection even after a Quartz backend lands.
- **Insufficient v1/v2 metadata** — those formats store at most a size hint,
  not origin, unit, or monitor topology. Cross-machine absolute replay is
  best-effort; prefer v3 source layout metadata when available.
"""
