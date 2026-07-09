# CursorTrack Roadmap

This document outlines the milestones and steps required to complete first-class support for Linux and macOS platforms in CursorTrack, and tracks follow-up work once each platform ships.

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

## Milestone 2: macOS (CoreGraphics) Support — ✅ Shipped in v0.3.0

Delivered by `MacOSBackend` (see [docs/architecture.md](docs/architecture.md#6-macos-coregraphics-notes)):
- Coordinate retrieval and emulation through `CoreGraphics` via `ctypes` (`CGEventGetLocation`/`CGEventCreateMouseEvent` + `CGEventPost`) — dependency-free, no `pyobjc` needed for this path.
- Click and scroll emulation through `CGEventCreateMouseEvent`/`CGEventCreateScrollWheelEvent` + `CGEventPost`.
- Global click/scroll capture through `pynput`'s macOS (Quartz event tap) hooks.
- Requires Accessibility permission (System Settings → Privacy & Security → Accessibility) for emulation and capture; `read_position`/`get_screen_size` work without it. `cursortrack doctor` reports the permission state.
- Validated in CI on `macos-latest` for import/init/`read_position`/`get_screen_size`/no-op logic; permission-gated round-trip tests (`set_position`, click/scroll capture) skip themselves there, since GitHub-hosted macOS runners cannot grant Accessibility permission — see `tests/test_macos_backend.py`.

### Follow-up work not yet done:
1. **Multi-display `get_screen_size()`.** Currently reports only `CGMainDisplayID()`'s pixel dimensions, matching the existing single-screen scope of the Windows backend.
2. **x1/x2 side-button capture identity.** `pynput`'s macOS listener cannot currently distinguish side buttons from a middle click (see architecture doc); fixing this would require either a `pynput` upstream fix or a custom Quartz event tap reading `kCGMouseEventButtonNumber` directly, bypassing `pynput` for capture.
