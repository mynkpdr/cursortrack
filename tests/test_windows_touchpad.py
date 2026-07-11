"""Pure tests for Windows Precision Touchpad scroll reconstruction."""

from __future__ import annotations

import ctypes
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import cursortrack.backends._windows_touchpad as touchpad_module
from cursortrack.backends._touchpad_scroll import (
    ContactFrameAssembler,
    ParsedContact,
    ParsedTouchpadReport,
    ScrollEventArbiter,
    TouchContact,
    TouchpadReportAssembler,
    TouchpadScrollTracker,
)
from cursortrack.backends._windows_hid import RID_DEVICE_INFO, RawInputRegistration
from cursortrack.backends._windows_touchpad import (
    PrecisionTouchpadScrollListener,
    windows_touchpad_capture_enabled,
)


def _two_contacts(y: float, x_offset: float = 0.0) -> tuple[TouchContact, TouchContact]:
    return (
        TouchContact(contact_id=1, x=0.4 + x_offset, y=y),
        TouchContact(contact_id=2, x=0.6 + x_offset, y=y),
    )


def test_two_finger_translation_emits_vertical_wheel_steps() -> None:
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(events.append, step_fraction=0.1)

    tracker.feed(_two_contacts(0.5), 1.0)
    tracker.feed(_two_contacts(0.34), 1.1)

    assert events == [(0, 1, 1.1)]
    assert tracker.scroll_active_at(1.15)


def test_scroll_fraction_is_accumulated_between_frames() -> None:
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(events.append, step_fraction=0.1)

    tracker.feed(_two_contacts(0.5), 1.0)
    tracker.feed(_two_contacts(0.44), 1.1)
    tracker.feed(_two_contacts(0.38), 1.2)

    assert events == [(0, 1, 1.2)]


def test_horizontal_scroll_and_reversed_direction_are_supported() -> None:
    normal: list[tuple[int, int, float]] = []
    reversed_events: list[tuple[int, int, float]] = []
    normal_tracker = TouchpadScrollTracker(normal.append, step_fraction=0.1)
    reversed_tracker = TouchpadScrollTracker(
        reversed_events.append,
        step_fraction=0.1,
        reverse_direction=True,
    )

    start = _two_contacts(0.5)
    moved_left = _two_contacts(0.5, x_offset=-0.12)
    normal_tracker.feed(start, 1.0)
    normal_tracker.feed(moved_left, 1.1)
    reversed_tracker.feed(start, 1.0)
    reversed_tracker.feed(moved_left, 1.1)

    assert normal == [(1, 0, 1.1)]
    assert reversed_events == [(-1, 0, 1.1)]


def test_finger_count_change_resets_motion_without_emitting_a_jump() -> None:
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(events.append, step_fraction=0.05)

    tracker.feed(_two_contacts(0.8), 1.0)
    tracker.feed((TouchContact(1, 0.4, 0.2),), 1.1)
    tracker.feed(_two_contacts(0.2), 1.2)

    assert events == []


def test_opposing_finger_motion_is_treated_as_pinch_not_scroll() -> None:
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(events.append, step_fraction=0.02)

    tracker.feed(
        (TouchContact(1, 0.4, 0.5), TouchContact(2, 0.6, 0.5)),
        1.0,
    )
    tracker.feed(
        (TouchContact(1, 0.32, 0.5), TouchContact(2, 0.68, 0.5)),
        1.1,
    )

    assert events == []
    assert not tracker.scroll_active_at(1.1)


def test_hybrid_reports_wait_until_both_contacts_have_updated() -> None:
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(events.append, step_fraction=0.1)
    start = _two_contacts(0.5)

    tracker.feed(start, 1.0)
    tracker.feed(
        (TouchContact(1, 0.4, 0.34), start[1]),
        1.1,
        updated_contact_ids=(1,),
    )
    assert events == []

    tracker.feed(
        _two_contacts(0.34),
        1.11,
        updated_contact_ids=(2,),
    )

    assert events == [(0, 1, 1.11)]


def test_per_frame_scroll_burst_is_bounded_without_delayed_debt() -> None:
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(
        events.append,
        step_fraction=0.01,
        max_steps_per_frame=4,
    )

    tracker.feed(_two_contacts(0.9), 1.0)
    tracker.feed(_two_contacts(0.1), 1.1)
    tracker.feed(_two_contacts(0.09), 1.2)

    assert events == [(0, 4, 1.1), (0, 1, 1.2)]


def test_contact_assembler_merges_hybrid_reports_and_expires_stale_contacts() -> None:
    assembler = ContactFrameAssembler(contact_timeout=0.08)

    first = assembler.update((ParsedContact(1, 0.2, 0.3, True),), 1.0)
    both = assembler.update((ParsedContact(2, 0.7, 0.3, True),), 1.02)
    expired = assembler.update((ParsedContact(2, 0.7, 0.4, True),), 1.10)

    assert first == (TouchContact(1, 0.2, 0.3),)
    assert both == (TouchContact(1, 0.2, 0.3), TouchContact(2, 0.7, 0.3))
    assert expired == (TouchContact(2, 0.7, 0.4),)


def test_contact_assembler_removes_explicit_lift_immediately() -> None:
    assembler = ContactFrameAssembler()
    assembler.update(
        (
            ParsedContact(1, 0.2, 0.3, True),
            ParsedContact(2, 0.7, 0.3, True),
        ),
        1.0,
    )

    contacts = assembler.update((ParsedContact(1, 0.2, 0.3, False),), 1.01)

    assert contacts == (TouchContact(2, 0.7, 0.3),)


def test_contact_assembler_excludes_unconfident_palm_contacts() -> None:
    assembler = ContactFrameAssembler()

    contacts = assembler.update(
        (
            ParsedContact(1, 0.2, 0.3, True, confident=True),
            ParsedContact(2, 0.7, 0.3, True, confident=False),
        ),
        1.0,
    )

    assert contacts == (TouchContact(1, 0.2, 0.3),)


def test_parallel_report_uses_only_declared_contact_count() -> None:
    assembler = TouchpadReportAssembler()
    contacts = (
        ParsedContact(1, 0.2, 0.3, True),
        ParsedContact(2, 0.7, 0.3, True),
        ParsedContact(0, 0.0, 0.0, False),
    )

    frame = assembler.update(ParsedTouchpadReport(scan_time=10, contact_count=2, contacts=contacts))

    assert frame == contacts[:2]


def test_hybrid_reports_are_joined_by_contact_count_and_scan_time() -> None:
    assembler = TouchpadReportAssembler()
    first = ParsedContact(1, 0.2, 0.3, True)
    second = ParsedContact(2, 0.7, 0.3, True)

    assert (
        assembler.update(ParsedTouchpadReport(scan_time=10, contact_count=2, contacts=(first,)))
        is None
    )
    frame = assembler.update(
        ParsedTouchpadReport(scan_time=10, contact_count=0, contacts=(second,))
    )

    assert frame == (first, second)


def test_hybrid_report_with_changed_scan_time_is_discarded() -> None:
    assembler = TouchpadReportAssembler()
    first = ParsedContact(1, 0.2, 0.3, True)
    second = ParsedContact(2, 0.7, 0.3, True)
    assembler.update(ParsedTouchpadReport(scan_time=10, contact_count=2, contacts=(first,)))

    frame = assembler.update(
        ParsedTouchpadReport(scan_time=11, contact_count=1, contacts=(second,))
    )

    assert frame == (second,)
    assert assembler.dropped_frames == 1


def test_rid_device_info_matches_native_win32_abi_size() -> None:
    assert ctypes.sizeof(RID_DEVICE_INFO) == 32


def test_scroll_arbiter_prefers_native_hook_when_it_arrives_first() -> None:
    emitted: list[tuple[str, int, int, float]] = []
    arbiter = ScrollEventArbiter(
        lambda source, dx, dy, timestamp: emitted.append((source, dx, dy, timestamp)),
        dedupe_window=0.08,
    )

    assert arbiter.emit_hook(0, -1, 1.0)
    assert not arbiter.emit_raw(0, -1, 1.04)
    assert emitted == [("hook", 0, -1, 1.0)]


def test_scroll_arbiter_suppresses_matching_hook_after_raw_scroll() -> None:
    emitted: list[tuple[str, int, int, float]] = []
    arbiter = ScrollEventArbiter(
        lambda source, dx, dy, timestamp: emitted.append((source, dx, dy, timestamp)),
        dedupe_window=0.08,
    )

    assert arbiter.emit_raw(0, -1, 1.0)
    assert not arbiter.emit_hook(0, -1, 1.02)
    assert emitted == [("raw", 0, -1, 1.0)]


def test_scroll_arbiter_preserves_unmatched_physical_wheel_event() -> None:
    emitted: list[tuple[str, int, int, float]] = []
    arbiter = ScrollEventArbiter(
        lambda source, dx, dy, timestamp: emitted.append((source, dx, dy, timestamp)),
        dedupe_window=0.08,
    )

    assert arbiter.emit_raw(0, -1, 1.0)
    assert arbiter.emit_hook(0, 1, 1.02)
    assert emitted == [("raw", 0, -1, 1.0), ("hook", 0, 1, 1.02)]


def test_scroll_arbiter_does_not_match_distant_out_of_order_events() -> None:
    emitted: list[tuple[str, int, int, float]] = []
    arbiter = ScrollEventArbiter(
        lambda source, dx, dy, timestamp: emitted.append((source, dx, dy, timestamp)),
        dedupe_window=0.08,
    )

    assert arbiter.emit_raw(0, -1, 100.0)
    assert arbiter.emit_hook(0, -1, 1.0)
    assert emitted == [("raw", 0, -1, 100.0), ("hook", 0, -1, 1.0)]


def test_scroll_arbiter_rolls_back_reservation_when_delivery_fails() -> None:
    emitted: list[tuple[str, int, int, float]] = []
    fail_first = True

    def emit(source: str, dx: int, dy: int, timestamp: float) -> None:
        nonlocal fail_first
        if fail_first:
            fail_first = False
            raise RuntimeError("consumer failed")
        emitted.append((source, dx, dy, timestamp))

    arbiter = ScrollEventArbiter(emit, dedupe_window=0.08)

    with pytest.raises(RuntimeError, match="consumer failed"):
        arbiter.emit_raw(0, -1, 1.0)
    assert arbiter.emit_hook(0, -1, 1.02)
    assert emitted == [("hook", 0, -1, 1.02)]


def test_scroll_arbiter_restores_consumed_match_when_partial_delivery_fails() -> None:
    emitted: list[tuple[str, int, int, float]] = []
    fail_delivery = False

    def emit(source: str, dx: int, dy: int, timestamp: float) -> None:
        if fail_delivery:
            raise RuntimeError("consumer failed")
        emitted.append((source, dx, dy, timestamp))

    arbiter = ScrollEventArbiter(emit, dedupe_window=0.08)
    assert arbiter.emit_raw(0, -1, 1.0)

    fail_delivery = True
    with pytest.raises(RuntimeError, match="consumer failed"):
        arbiter.emit_hook(0, -2, 1.02)

    fail_delivery = False
    assert arbiter.emit_hook(0, -2, 1.03)
    assert emitted == [("raw", 0, -1, 1.0), ("hook", 0, -1, 1.03)]


class _FakeUser32:
    def __init__(self) -> None:
        self.quit = threading.Event()
        self.calls: list[str] = []
        self.RegisterClassExW = self._register_class
        self.CreateWindowExW = self._create_window
        self.DefWindowProcW = self._default_window_proc
        self.GetMessageW = self._get_message
        self.TranslateMessage = self._translate_message
        self.DispatchMessageW = self._dispatch_message
        self.PostThreadMessageW = self._post_thread_message
        self.DestroyWindow = self._destroy_window
        self.UnregisterClassW = self._unregister_class

    def _register_class(self, _window_class: object) -> int:
        self.calls.append("class:register")
        return 1

    def _create_window(self, *_args: object) -> int:
        self.calls.append("window:create")
        return 101

    def _default_window_proc(self, *_args: object) -> int:
        return 0

    def _get_message(self, *_args: object) -> int:
        self.quit.wait(timeout=1.0)
        return 0

    def _translate_message(self, *_args: object) -> int:
        return 1

    def _dispatch_message(self, *_args: object) -> int:
        return 0

    def _post_thread_message(self, *_args: object) -> int:
        self.calls.append("thread:quit")
        self.quit.set()
        return 1

    def _destroy_window(self, *_args: object) -> int:
        self.calls.append("window:destroy")
        return 1

    def _unregister_class(self, *_args: object) -> int:
        self.calls.append("class:unregister")
        return 1


class _FakeRawInputApi:
    def __init__(self, existing_target: int | None = None) -> None:
        self.user32 = _FakeUser32()
        self.kernel32 = SimpleNamespace(
            GetModuleHandleW=lambda _name: 1,
            GetCurrentThreadId=lambda: 321,
        )
        self.hid = SimpleNamespace()
        self.target = existing_target
        self.calls: list[str] = []

    def touchpad_devices(self) -> tuple[int, ...]:
        return (11,)

    def touchpad_registration(self) -> RawInputRegistration | None:
        if self.target is None:
            return None
        return RawInputRegistration(target=self.target, flags=0)

    def register_touchpad(self, window: int) -> None:
        self.calls.append(f"raw:register:{window}")
        self.target = window

    def remove_touchpad(self) -> None:
        self.calls.append("raw:remove")
        self.target = None


class _FakeLayout:
    def __init__(self, _api: object, _device: int) -> None:
        pass


def _listener_with_fake_api(
    monkeypatch: pytest.MonkeyPatch,
    api: _FakeRawInputApi,
) -> PrecisionTouchpadScrollListener:
    monkeypatch.setattr(touchpad_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(touchpad_module, "windows_touchpad_pan_enabled", lambda: True)
    monkeypatch.setattr(touchpad_module, "_HidDeviceLayout", _FakeLayout)
    return PrecisionTouchpadScrollListener(
        lambda *_: None,
        step_fraction=0.01,
        reverse_direction=False,
        api_factory=lambda: api,  # type: ignore[arg-type]
    )


def test_listener_registers_and_removes_only_its_owned_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeRawInputApi()
    listener = _listener_with_fake_api(monkeypatch, api)

    assert listener.start()
    listener.stop()

    assert api.calls == ["raw:register:101", "raw:remove"]
    assert api.target is None
    assert listener.runtime_error is None
    assert not listener.running


def test_listener_health_detects_replaced_raw_input_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeRawInputApi()
    listener = _listener_with_fake_api(monkeypatch, api)

    assert listener.start()
    api.target = 77
    with pytest.warns(RuntimeWarning, match="registration was replaced"):
        error = listener.check_health()
    listener.stop()

    assert error == "Precision Touchpad Raw Input registration was replaced."
    assert api.target == 77


def test_listener_refuses_to_replace_an_existing_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeRawInputApi(existing_target=77)
    listener = _listener_with_fake_api(monkeypatch, api)

    with pytest.raises(RuntimeError, match="already owns"):
        listener.start()

    assert api.calls == []
    assert api.target == 77
    assert not listener.running


def test_listener_rolls_back_when_ownership_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnverifiableApi(_FakeRawInputApi):
        def __init__(self) -> None:
            super().__init__()
            self.queries = 0

        def touchpad_registration(self) -> RawInputRegistration | None:
            self.queries += 1
            if self.queries >= 2:
                raise OSError("ownership query failed")
            return None

    api = UnverifiableApi()
    listener = _listener_with_fake_api(monkeypatch, api)

    with (
        pytest.warns(RuntimeWarning, match="ownership check"),
        pytest.raises(RuntimeError, match="ownership query failed"),
    ):
        listener.start()

    assert api.calls == ["raw:register:101", "raw:remove"]
    assert api.target is None


def test_listener_rejects_a_second_cursortrack_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _listener_with_fake_api(monkeypatch, _FakeRawInputApi())
    second = _listener_with_fake_api(monkeypatch, _FakeRawInputApi())

    assert first.start()
    try:
        with pytest.raises(RuntimeError, match="Another CursorTrack"):
            second.start()
    finally:
        second.stop()
        first.stop()


def test_listener_reports_shutdown_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class StuckThread:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            assert timeout == 0.01

    api = _FakeRawInputApi()
    listener = PrecisionTouchpadScrollListener(
        lambda *_: None,
        step_fraction=0.01,
        reverse_direction=False,
    )
    listener._api = api  # type: ignore[assignment]
    listener._thread = StuckThread()  # type: ignore[assignment]
    listener._thread_id = 321
    listener._window = 101
    monkeypatch.setattr(touchpad_module, "SHUTDOWN_TIMEOUT", 0.01)

    with pytest.warns(RuntimeWarning, match="did not stop"):
        listener.stop()

    assert listener.runtime_error == "Precision Touchpad listener did not stop within 0.01 seconds."
    assert listener._thread is not None


def test_zero_handle_report_is_rejected_with_multiple_touchpads() -> None:
    listener = PrecisionTouchpadScrollListener(
        lambda *_: None,
        step_fraction=0.01,
        reverse_direction=False,
    )
    listener._api = SimpleNamespace(raw_input=lambda _handle: (0, (b"report",)))  # type: ignore[assignment]
    listener._devices = {1: Any, 2: Any}  # type: ignore[dict-item]

    with pytest.raises(RuntimeError, match="multiple compatible touchpads"):
        listener._handle_raw_input(123)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), (" true ", True), ("off", False), ("0", False)],
)
def test_touchpad_capture_environment_is_parsed_strictly(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("CURSORTRACK_WINDOWS_TOUCHPAD", value)
    assert windows_touchpad_capture_enabled() is expected


def test_invalid_touchpad_capture_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSORTRACK_WINDOWS_TOUCHPAD", "sometimes")
    with pytest.raises(ValueError, match="CURSORTRACK_WINDOWS_TOUCHPAD"):
        windows_touchpad_capture_enabled()
