# CursorTrack Roadmap

This document outlines the platform and portable-replay milestones for CursorTrack.

---

## Milestone 1: Linux (X11) Support — ✅ Shipped in v0.2.0

Delivered by `LinuxBackend` (see [docs/architecture.md](docs/architecture.md#5-linux-x11wayland-notes)):
- Coordinate retrieval and warping through `libX11` via `ctypes` (`XQueryPointer`/`XWarpPointer`) — dependency-free, no `python-xlib` needed.
- Click and scroll emulation through the X11 Test Extension (`XTestFakeButtonEvent`).
- Global click/scroll capture through `pynput`'s X11 hooks.
- Works on X11 sessions and against XWayland on Wayland desktops; validated in CI under Xvfb across Python 3.9–3.14.

---

## Milestone 1b: Native Wayland Support

Wayland prevents clients from capturing global coordinate information or driving other windows directly due to sandboxing, so input delivered to native Wayland clients is out of reach for the X11-based backend.

### Key Tasks:
1. Research and implement Portal APIs (`org.freedesktop.portal.RemoteDesktop`) for permission-prompted capture and injection.
2. Alternatively, read raw events from `/dev/input/` (requires root/`input` group permissions) for capture-only workflows.

---

## Milestone 2: macOS (Quartz) Support

Mac systems handle mouse and global event interception through Cocoa/Quartz APIs.

### Key Tasks:
1. **Quartz Integration**:
   - Import Quartz frameworks (`pyobjc-framework-Quartz` / `pyobjc-core`).
   - Read position via `CGEventGetLocation(CGEventCreate(None))` to obtain high-precision screen coordinates.
   - Move cursor via `CGWarpMouseCursorPosition` or `CGEventCreateMouseEvent`.
2. **Click & Scroll Emulation**:
   - Post mouse events directly into the event stream using `CGEventPost` to simulate clicks and scroll wheel deltas.
3. **Event Listening Hook**:
   - Setup a background thread mapping dynamic Quartz Event Taps. This allows capturing mouse clicks and scroll coordinates globally (requires Accessibility permission).
   - Alternatively, fall back to `pynput`'s macOS AppKit listener.

---

## Milestone 3: Portable Replay Contract

The v1/v2 file format is OS-neutral but stores insufficient display metadata to
replay safely across different monitor layouts, origins, scales, or coordinate
units.

### Key Tasks:
1. Review and accept [RFC 0001](docs/rfcs/0001-portable-session-v3.md).
2. Add v3 source-layout metadata, length-framed events, and independently
   checksummed compression chunks while preserving v1/v2 readers.
3. Add a no-injection compatibility preview and strict mismatch detection.
4. Add explicit, opt-in coordinate mapping modes; never silently scale or clamp.
