"""Precision Touchpad listener and scroll-capture orchestration for Windows."""

from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from cursortrack.backends._touchpad_scroll import (
    DEFAULT_STEP_FRACTION,
    ContactFrameAssembler,
    TouchpadReportAssembler,
    TouchpadScrollTracker,
)
from cursortrack.backends._windows_hid import (
    MSG,
    WM_INPUT,
    WM_INPUT_DEVICE_CHANGE,
    WM_QUIT,
    WNDCLASSEXW,
    WNDPROC,
    _HidDeviceLayout,
    _last_error,
    _Win32Api,
)

__all__ = [
    "PrecisionTouchpadScrollListener",
    "TouchpadProbe",
    "probe_precision_touchpad",
    "windows_touchpad_capture_enabled",
    "windows_touchpad_step_fraction",
]

STARTUP_TIMEOUT = 1.5
SHUTDOWN_TIMEOUT = 2.0
OWNERSHIP_CHECK_INTERVAL = 0.5

_REGISTRATION_LOCK = threading.Lock()
_REGISTRATION_OWNER: Optional[int] = None


@dataclass(frozen=True)
class TouchpadProbe:
    """Result of validating standardized Precision Touchpad HID devices."""

    device_count: int
    reverse_direction: bool
    compatible_device_count: int = 0
    pan_enabled: bool = True
    error: Optional[str] = None
    compatibility_errors: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.compatible_device_count > 0 and self.pan_enabled and self.error is None


@dataclass
class _DeviceState:
    layout: _HidDeviceLayout
    reports: TouchpadReportAssembler
    contacts: ContactFrameAssembler
    tracker: TouchpadScrollTracker

    def reset(self) -> None:
        self.reports.reset()
        self.contacts.reset()
        self.tracker.reset()


class PrecisionTouchpadScrollListener:
    """Background Raw Input listener for Windows Precision Touchpad scrolling."""

    def __init__(
        self,
        on_scroll: Callable[[int, int, float], None],
        *,
        on_activity: Optional[Callable[[float], None]] = None,
        step_fraction: Optional[float] = None,
        reverse_direction: Optional[bool] = None,
        api_factory: Callable[[], _Win32Api] = _Win32Api,
    ) -> None:
        self._step_fraction = (
            windows_touchpad_step_fraction() if step_fraction is None else step_fraction
        )
        if not math.isfinite(self._step_fraction) or self._step_fraction <= 0:
            raise ValueError("step_fraction must be finite and positive.")
        self._reverse_direction = (
            windows_touchpad_reverse_direction() if reverse_direction is None else reverse_direction
        )
        self._on_scroll = on_scroll
        self._on_activity = on_activity
        self._api_factory = api_factory
        self._api: Optional[_Win32Api] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._window: Optional[int] = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._state_lock = threading.RLock()
        self._startup_error: Optional[BaseException] = None
        self._runtime_error: Optional[str] = None
        self._known_devices: tuple[int, ...] = ()
        self._devices: dict[int, _DeviceState] = {}
        self._wndproc: Optional[Any] = None
        self._owns_process_registration = False
        self._last_ownership_check = -math.inf
        self._class_name = f"CursorTrackTouchpad_{os.getpid()}_{id(self):x}"

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive() and self._window is not None

    @property
    def runtime_error(self) -> Optional[str]:
        with self._state_lock:
            return self._runtime_error

    @property
    def thread_alive(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def check_health(self) -> Optional[str]:
        """Return and retain any listener, parser, or ownership failure."""
        error = self.runtime_error
        if error is not None:
            return error
        with self._state_lock:
            thread = self._thread
            api = self._api
            window = self._window
            now = time.monotonic()
            should_check_ownership = (
                thread is not None
                and thread.is_alive()
                and api is not None
                and window is not None
                and now - self._last_ownership_check >= OWNERSHIP_CHECK_INTERVAL
            )
            if should_check_ownership:
                self._last_ownership_check = now
        if thread is None:
            return None
        if not thread.is_alive() or window is None:
            message = "Precision Touchpad listener stopped unexpectedly."
            self._report_runtime_error(message)
            return message
        if should_check_ownership:
            assert api is not None
            try:
                registration = api.touchpad_registration()
            except Exception as ownership_error:
                message = f"Precision Touchpad ownership check failed: {ownership_error}"
                self._report_runtime_error(message)
                return message
            if registration is None or registration.target != window:
                message = "Precision Touchpad Raw Input registration was replaced."
                self._report_runtime_error(message)
                return message
        return None

    def start(self) -> bool:
        """Start listening; return False when no usable touchpad is available."""
        with self._state_lock:
            if self.running:
                return True
            if self._thread is not None:
                if self._thread.is_alive():
                    raise RuntimeError(
                        "Precision Touchpad listener is already starting or stopping."
                    )
                self._clear_stopped_state()
            if not sys.platform.startswith("win"):
                return False
            if not windows_touchpad_pan_enabled():
                return False

            self._api = self._api_factory()
            self._known_devices = self._api.touchpad_devices()
            if not self._known_devices:
                self._api = None
                return False
            self._devices, errors = self._build_device_states(self._known_devices)
            if not self._devices:
                self._api = None
                detail = "; ".join(errors) if errors else "no compatible HID descriptors"
                raise RuntimeError(f"Precision Touchpad is detected but unsupported: {detail}")

            try:
                self._claim_process_registration()
            except BaseException:
                self._api = None
                self._known_devices = ()
                self._devices = {}
                raise
            self._ready.clear()
            self._stop_requested.clear()
            self._startup_error = None
            self._runtime_error = None
            self._last_ownership_check = -math.inf
            self._thread = threading.Thread(
                target=self._run,
                name="cursortrack-touchpad",
                daemon=True,
            )
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._release_process_registration()
                raise

        if not self._ready.wait(STARTUP_TIMEOUT):
            self._stop_requested.set()
            self.stop()
            raise RuntimeError("Precision Touchpad listener did not become ready.")
        error = self._get_startup_error()
        if error is not None:
            self.stop()
            raise RuntimeError(f"Precision Touchpad listener failed: {error}") from error
        return self.running

    def stop(self) -> None:
        """Stop the message loop without abandoning registration ownership."""
        with self._state_lock:
            api = self._api
            thread = self._thread
            thread_id = self._thread_id
            self._stop_requested.set()

        if thread is threading.current_thread():
            return
        post_failed = bool(
            thread is not None
            and thread.is_alive()
            and api is not None
            and thread_id
            and not api.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        )
        # Before CreateWindowExW there may be no message queue yet. The worker
        # checks _stop_requested before registering and blocking.
        if post_failed and self._window is not None:
            self._report_runtime_error(
                f"PostThreadMessageW(WM_QUIT) failed with error {_last_error()}."
            )
        if thread is not None:
            thread.join(timeout=SHUTDOWN_TIMEOUT)
            if thread.is_alive():
                self._report_runtime_error(
                    f"Precision Touchpad listener did not stop within {SHUTDOWN_TIMEOUT:g} seconds."
                )
                return

        with self._state_lock:
            self._clear_stopped_state()

    def _claim_process_registration(self) -> None:
        global _REGISTRATION_OWNER
        with _REGISTRATION_LOCK:
            if _REGISTRATION_OWNER not in (None, id(self)):
                raise RuntimeError(
                    "Another CursorTrack Precision Touchpad listener owns Raw Input "
                    "registration in this process."
                )
            _REGISTRATION_OWNER = id(self)
            self._owns_process_registration = True

    def _release_process_registration(self) -> None:
        global _REGISTRATION_OWNER
        with _REGISTRATION_LOCK:
            if id(self) == _REGISTRATION_OWNER:
                _REGISTRATION_OWNER = None
            self._owns_process_registration = False

    def _build_device_states(
        self,
        devices: tuple[int, ...],
    ) -> tuple[dict[int, _DeviceState], list[str]]:
        assert self._api is not None
        states: dict[int, _DeviceState] = {}
        errors: list[str] = []
        for device in devices:
            try:
                layout = _HidDeviceLayout(self._api, device)
            except Exception as error:
                errors.append(f"device {device}: {error}")
                continue
            tracker = TouchpadScrollTracker(
                lambda event: self._on_scroll(*event),
                step_fraction=self._step_fraction,
                reverse_direction=self._reverse_direction,
                on_activity=self._on_activity,
            )
            states[device] = _DeviceState(
                layout=layout,
                reports=TouchpadReportAssembler(),
                contacts=ContactFrameAssembler(),
                tracker=tracker,
            )
        return states, errors

    def _refresh_devices(self) -> None:
        assert self._api is not None
        known_devices = self._api.touchpad_devices()
        devices, errors = self._build_device_states(known_devices)
        self._known_devices = known_devices
        self._devices = devices
        if known_devices and not devices:
            detail = "; ".join(errors) if errors else "no compatible HID descriptors"
            raise RuntimeError(f"No usable Precision Touchpad remains: {detail}")

    def _get_startup_error(self) -> Optional[BaseException]:
        with self._state_lock:
            return self._startup_error

    def _run(self) -> None:
        assert self._api is not None
        api = self._api
        instance = api.kernel32.GetModuleHandleW(None)
        with self._state_lock:
            self._thread_id = int(api.kernel32.GetCurrentThreadId())

        def window_proc(
            window: int,
            message: int,
            wparam: int,
            lparam: int,
        ) -> int:
            if message == WM_INPUT:
                try:
                    self._handle_raw_input(lparam)
                except Exception as error:
                    self._report_runtime_error(f"Precision Touchpad report failed: {error}")
                return int(api.user32.DefWindowProcW(window, message, wparam, lparam))
            if message == WM_INPUT_DEVICE_CHANGE:
                try:
                    self._refresh_devices()
                except Exception as error:
                    self._report_runtime_error(f"Precision Touchpad device refresh failed: {error}")
            return int(api.user32.DefWindowProcW(window, message, wparam, lparam))

        self._wndproc = WNDPROC(window_proc)
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name

        class_registered = False
        raw_registered = False
        try:
            if self._stop_requested.is_set():
                return
            if not api.user32.RegisterClassExW(ctypes.byref(window_class)):
                raise OSError(_last_error(), "RegisterClassExW failed")
            class_registered = True
            window = api.user32.CreateWindowExW(
                0,
                self._class_name,
                "CursorTrack Precision Touchpad",
                0,
                0,
                0,
                0,
                0,
                ctypes.c_void_p(-3),
                None,
                instance,
                None,
            )
            if not window:
                raise OSError(_last_error(), "CreateWindowExW failed")
            with self._state_lock:
                self._window = int(window)

            if self._stop_requested.is_set():
                return
            existing = api.touchpad_registration()
            if existing is not None and existing.target != int(window):
                raise RuntimeError(
                    "Another component already owns Precision Touchpad Raw Input "
                    f"registration for window {existing.target}."
                )
            api.register_touchpad(int(window))
            raw_registered = True
            current = api.touchpad_registration()
            if current is None or current.target != int(window):
                raise RuntimeError("Precision Touchpad Raw Input ownership could not be verified.")
            self._ready.set()

            if self._stop_requested.is_set():
                return
            message = MSG()
            while not self._stop_requested.is_set():
                result = api.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise OSError(_last_error(), "GetMessageW failed")
                api.user32.TranslateMessage(ctypes.byref(message))
                api.user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as error:
            if not self._ready.is_set():
                with self._state_lock:
                    self._startup_error = error
                self._ready.set()
            else:
                self._report_runtime_error(f"Precision Touchpad listener failed: {error}")
        finally:
            if raw_registered:
                remove_registration = False
                try:
                    current = api.touchpad_registration()
                    remove_registration = current is not None and current.target == int(
                        self._window or 0
                    )
                except Exception as error:
                    self._report_runtime_error(
                        f"Precision Touchpad ownership check during cleanup failed: {error}"
                    )
                    # Registration succeeded and ownership cannot be queried.
                    # Prefer removing our potentially orphaned target to leaving
                    # a dead hidden window registered for the process.
                    remove_registration = True
                if remove_registration:
                    try:
                        api.remove_touchpad()
                    except Exception as error:
                        self._report_runtime_error(
                            f"Precision Touchpad registration cleanup failed: {error}"
                        )
            if self._window is not None and not api.user32.DestroyWindow(
                ctypes.c_void_p(self._window)
            ):
                self._report_runtime_error(f"DestroyWindow failed with error {_last_error()}.")
            if class_registered and not api.user32.UnregisterClassW(self._class_name, instance):
                self._report_runtime_error(f"UnregisterClassW failed with error {_last_error()}.")
            with self._state_lock:
                self._window = None
                self._thread_id = 0
            self._release_process_registration()
            self._ready.set()

    def _handle_raw_input(self, raw_input_handle: int) -> None:
        assert self._api is not None
        device, reports = self._api.raw_input(raw_input_handle)
        if not reports:
            return
        if not device:
            if len(self._devices) != 1:
                raise RuntimeError(
                    "Precision Touchpad report has no device handle and cannot be "
                    "matched because multiple compatible touchpads are active."
                )
            device = next(iter(self._devices))
        state = self._devices.get(device)
        if state is None:
            self._refresh_devices()
            state = self._devices.get(device)
        if state is None:
            raise RuntimeError(f"Precision Touchpad device {device} has no compatible descriptor.")

        for report in reports:
            parsed_report = state.layout.parse(report)
            frame = state.reports.update(parsed_report)
            if frame is None:
                continue
            timestamp = time.perf_counter()
            contacts = state.contacts.replace(frame, timestamp)
            state.tracker.feed(contacts, timestamp)

    def _report_runtime_error(self, message: str) -> None:
        with self._state_lock:
            if self._runtime_error is not None:
                return
            self._runtime_error = message
        warnings.warn(message, RuntimeWarning, stacklevel=2)

    def _clear_stopped_state(self) -> None:
        self._thread = None
        self._thread_id = 0
        self._window = None
        self._api = None
        self._known_devices = ()
        self._devices = {}
        self._wndproc = None
        self._startup_error = None
        self._last_ownership_check = -math.inf
        self._stop_requested.clear()
        if self._owns_process_registration:
            self._release_process_registration()


def probe_precision_touchpad() -> TouchpadProbe:
    """Detect touchpads and validate the descriptors required for capture."""
    reverse = windows_touchpad_reverse_direction()
    pan_enabled = windows_touchpad_pan_enabled()
    if not sys.platform.startswith("win"):
        return TouchpadProbe(
            device_count=0,
            compatible_device_count=0,
            reverse_direction=reverse,
            pan_enabled=pan_enabled,
            error="Windows-only",
        )
    try:
        api = _Win32Api()
        devices = api.touchpad_devices()
        compatible = 0
        errors: list[str] = []
        for device in devices:
            try:
                _HidDeviceLayout(api, device)
            except Exception as error:
                errors.append(f"device {device}: {error}")
            else:
                compatible += 1
        return TouchpadProbe(
            device_count=len(devices),
            compatible_device_count=compatible,
            reverse_direction=reverse,
            pan_enabled=pan_enabled,
            compatibility_errors=tuple(errors),
        )
    except Exception as error:
        return TouchpadProbe(
            device_count=0,
            compatible_device_count=0,
            reverse_direction=reverse,
            pan_enabled=pan_enabled,
            error=str(error),
        )


def windows_touchpad_capture_enabled(default: bool = False) -> bool:
    """Resolve the shared Raw Input opt-in environment setting."""
    return _boolean_environment("CURSORTRACK_WINDOWS_TOUCHPAD", default)


def windows_touchpad_reverse_direction() -> bool:
    """Read the user's Windows two-finger scroll-direction preference."""
    value = _precision_touchpad_registry_value("ScrollDirection")
    return bool(value) if value is not None else False


def windows_touchpad_pan_enabled() -> bool:
    """Return whether Windows two-finger panning is enabled for the user."""
    value = _precision_touchpad_registry_value("PanEnabled")
    return bool(value) if value is not None else True


def _precision_touchpad_registry_value(name: str) -> Optional[int]:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\PrecisionTouchPad"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
        return int(value)
    except (OSError, TypeError, ValueError):
        return None


def _boolean_environment(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of 1/true/yes/on or 0/false/no/off (got {raw!r}).")


def windows_touchpad_step_fraction() -> float:
    """Return the validated touchpad-to-wheel sensitivity setting."""
    raw = os.environ.get("CURSORTRACK_TOUCHPAD_STEP_FRACTION")
    if raw is None:
        return DEFAULT_STEP_FRACTION
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(
            "CURSORTRACK_TOUCHPAD_STEP_FRACTION must be a number between 0.002 and 0.1."
        ) from error
    if not math.isfinite(value) or not 0.002 <= value <= 0.1:
        raise ValueError("CURSORTRACK_TOUCHPAD_STEP_FRACTION must be between 0.002 and 0.1.")
    return value
