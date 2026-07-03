# CursorTrack Roadmap

This document outlines the milestones and steps required to complete first-class support for Linux and macOS platforms in CursorTrack.

---

## Milestone 1: Linux (X11 & Wayland) Support

Linux input tracking is split into two window manager architectures: X11 (legacy, still widely used) and Wayland (modern, sandboxed, default on Ubuntu/Fedora).

### Key Tasks:
1. **Coordinate Retrieval and Emulation (X11)**:
   - Use `python-xlib` (already standard and lightweight) to read and set cursor coordinates on the active display.
   - Use X11 Test Extension (`XTest`) for zero-dependency clicks and scroll emulation.
2. **Coordinate Retrieval and Emulation (Wayland)**:
   - Wayland prevents clients from capturing global coordinate information or driving other windows directly due to sandboxing.
   - Research and implement Portal APIs (`org.freedesktop.portal.RemoteDesktop`) or read raw events from `/dev/input/` (requires root/input group permissions).
3. **Global Listener Hook**:
   - Use `pynput` as the primary background event listening engine (which falls back to X11 hooks).

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
