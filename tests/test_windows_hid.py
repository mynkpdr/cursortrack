"""Unit tests for Win32 Raw Input/HID transport boundaries."""

from __future__ import annotations

import ctypes
from typing import Any

import pytest

from cursortrack.backends._touchpad_scroll import (
    ContactFrameAssembler,
    TouchpadReportAssembler,
    TouchpadScrollTracker,
)
from cursortrack.backends._windows_hid import (
    HIDP_BUTTON_CAPS,
    HIDP_CAPS,
    HIDP_STATUS_SUCCESS,
    HIDP_VALUE_CAPS,
    RAWINPUTDEVICE,
    RAWINPUTHEADER,
    RID_DEVICE_INFO,
    RIM_TYPEHID,
    USAGE_DIGITIZER_CONFIDENCE,
    USAGE_DIGITIZER_CONTACT_COUNT,
    USAGE_DIGITIZER_CONTACT_ID,
    USAGE_DIGITIZER_SCAN_TIME,
    USAGE_DIGITIZER_TIP_SWITCH,
    USAGE_GENERIC_X,
    USAGE_GENERIC_Y,
    USAGE_PAGE_DIGITIZER,
    USAGE_PAGE_GENERIC,
    _HidDeviceLayout,
    _Win32Api,
)


def _api_with_user32(user32: object) -> _Win32Api:
    api = object.__new__(_Win32Api)
    api.user32 = user32
    return api


def _hid_packet(device: int, report_size: int, report_count: int, data: bytes) -> bytes:
    header = RAWINPUTHEADER()
    header.dwType = RIM_TYPEHID
    header.dwSize = ctypes.sizeof(RAWINPUTHEADER) + 8 + len(data)
    header.hDevice = device
    return (
        bytes(header)
        + report_size.to_bytes(4, "little")
        + report_count.to_bytes(4, "little")
        + data
    )


class _RawInputUser32:
    def __init__(self, packet: bytes) -> None:
        self.packet = packet

    def GetRawInputData(  # noqa: N802 - mirrors the Win32 API
        self,
        _handle: object,
        _command: int,
        output: Any,
        size_ref: Any,
        _header_size: int,
    ) -> int:
        size = ctypes.cast(size_ref, ctypes.POINTER(ctypes.c_uint32))
        size.contents.value = len(self.packet)
        if output is None:
            return 0
        ctypes.memmove(output, self.packet, len(self.packet))
        return len(self.packet)


def test_raw_input_splits_batched_hid_reports() -> None:
    api = _api_with_user32(_RawInputUser32(_hid_packet(44, 3, 2, b"abcdef")))

    device, reports = api.raw_input(123)

    assert device == 44
    assert reports == (b"abc", b"def")


def test_raw_input_rejects_malformed_report_lengths() -> None:
    api = _api_with_user32(_RawInputUser32(_hid_packet(44, 100, 2, b"short")))

    with pytest.raises(RuntimeError, match="lengths"):
        api.raw_input(123)


class _RegisteredDevicesUser32:
    def GetRegisteredRawInputDevices(  # noqa: N802 - mirrors the Win32 API
        self,
        registrations: Any,
        count_ref: Any,
        _structure_size: int,
    ) -> int:
        count = ctypes.cast(count_ref, ctypes.POINTER(ctypes.c_uint32))
        count.contents.value = 1
        if registrations is None:
            return 0
        registration = ctypes.cast(registrations, ctypes.POINTER(RAWINPUTDEVICE)).contents
        registration.usUsagePage = 0x0D
        registration.usUsage = 0x05
        registration.dwFlags = 0x100
        registration.hwndTarget = 77
        return 1


def test_registered_touchpad_target_is_read_before_claiming_ownership() -> None:
    api = _api_with_user32(_RegisteredDevicesUser32())

    registration = api.touchpad_registration()

    assert registration is not None
    assert registration.target == 77
    assert registration.flags == 0x100


def test_fixed_width_hid_structures_match_native_abi() -> None:
    assert ctypes.sizeof(RID_DEVICE_INFO) == 32
    assert ctypes.sizeof(HIDP_CAPS) == 64
    assert ctypes.sizeof(HIDP_BUTTON_CAPS) == 72
    assert ctypes.sizeof(HIDP_VALUE_CAPS) == 72


class _DescriptorHid:
    def __init__(self, *, contact_id: bool = True, confidence: bool = True) -> None:
        self.contact_id = contact_id
        self.confidence = confidence

    def HidP_GetCaps(self, _preparsed: object, caps_ref: Any) -> int:  # noqa: N802
        caps = ctypes.cast(caps_ref, ctypes.POINTER(HIDP_CAPS)).contents
        caps.NumberInputValueCaps = 5 if self.contact_id else 4
        caps.NumberInputButtonCaps = 2 if self.confidence else 1
        return HIDP_STATUS_SUCCESS

    def HidP_GetValueCaps(  # noqa: N802
        self,
        _report_type: int,
        capabilities: Any,
        count_ref: Any,
        _preparsed: object,
    ) -> int:
        usages = [
            (USAGE_PAGE_GENERIC, USAGE_GENERIC_X, 1),
            (USAGE_PAGE_GENERIC, USAGE_GENERIC_Y, 1),
            (USAGE_PAGE_DIGITIZER, USAGE_DIGITIZER_CONTACT_COUNT, 0),
            (USAGE_PAGE_DIGITIZER, USAGE_DIGITIZER_SCAN_TIME, 0),
        ]
        if self.contact_id:
            usages.append((USAGE_PAGE_DIGITIZER, USAGE_DIGITIZER_CONTACT_ID, 1))
        count = ctypes.cast(count_ref, ctypes.POINTER(ctypes.c_uint16))
        count.contents.value = len(usages)
        for index, (page, usage, link) in enumerate(usages):
            capability = capabilities[index]
            capability.UsagePage = page
            capability.LinkCollection = link
            capability.NotRange.Usage = usage
            capability.LogicalMin = 0
            capability.LogicalMax = 1000
        return HIDP_STATUS_SUCCESS

    def HidP_GetButtonCaps(  # noqa: N802
        self,
        _report_type: int,
        capabilities: Any,
        count_ref: Any,
        _preparsed: object,
    ) -> int:
        usages = [USAGE_DIGITIZER_TIP_SWITCH]
        if self.confidence:
            usages.append(USAGE_DIGITIZER_CONFIDENCE)
        count = ctypes.cast(count_ref, ctypes.POINTER(ctypes.c_uint16))
        count.contents.value = len(usages)
        for index, usage in enumerate(usages):
            capability = capabilities[index]
            capability.UsagePage = USAGE_PAGE_DIGITIZER
            capability.LinkCollection = 1
            capability.NotRange.Usage = usage
        return HIDP_STATUS_SUCCESS

    def HidP_GetUsageValue(  # noqa: N802
        self,
        _report_type: int,
        _usage_page: int,
        _link: int,
        usage: int,
        value_ref: Any,
        _preparsed: object,
        report: Any,
        report_length: int,
    ) -> int:
        contact_id, scan_time, contact_count, y_percent = ctypes.string_at(report, report_length)
        values = {
            USAGE_GENERIC_X: 400 if contact_id == 1 else 600,
            USAGE_GENERIC_Y: y_percent * 10,
            USAGE_DIGITIZER_CONTACT_ID: contact_id,
            USAGE_DIGITIZER_CONTACT_COUNT: contact_count,
            USAGE_DIGITIZER_SCAN_TIME: scan_time,
        }
        value = ctypes.cast(value_ref, ctypes.POINTER(ctypes.c_uint32))
        value.contents.value = values[usage]
        return HIDP_STATUS_SUCCESS

    def HidP_GetUsages(  # noqa: N802
        self,
        _report_type: int,
        _usage_page: int,
        _link: int,
        usages: Any,
        usage_count_ref: Any,
        _preparsed: object,
        _report: Any,
        _report_length: int,
    ) -> int:
        usages[0] = USAGE_DIGITIZER_TIP_SWITCH
        usages[1] = USAGE_DIGITIZER_CONFIDENCE
        usage_count = ctypes.cast(usage_count_ref, ctypes.POINTER(ctypes.c_uint32))
        usage_count.contents.value = 2
        return HIDP_STATUS_SUCCESS


class _DescriptorApi:
    def __init__(self, hid: _DescriptorHid) -> None:
        self.hid = hid

    def preparsed_data(self, _device: int) -> ctypes.Array[Any]:
        return ctypes.create_string_buffer(1)


def test_descriptor_requires_complete_precision_touchpad_contact_usages() -> None:
    _HidDeviceLayout(_DescriptorApi(_DescriptorHid()), 11)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="Contact ID"):
        _HidDeviceLayout(  # type: ignore[arg-type]
            _DescriptorApi(_DescriptorHid(contact_id=False)),
            11,
        )
    with pytest.raises(RuntimeError, match="Confidence"):
        _HidDeviceLayout(  # type: ignore[arg-type]
            _DescriptorApi(_DescriptorHid(confidence=False)),
            11,
        )


def test_hybrid_hid_reports_reconstruct_two_finger_scroll() -> None:
    layout = _HidDeviceLayout(  # type: ignore[arg-type]
        _DescriptorApi(_DescriptorHid()),
        11,
    )
    reports = TouchpadReportAssembler()
    contacts = ContactFrameAssembler()
    events: list[tuple[int, int, float]] = []
    tracker = TouchpadScrollTracker(events.append, step_fraction=0.1)

    frames = (
        ((1, 10, 2, 80), (2, 10, 0, 80), 1.0),
        ((1, 11, 2, 60), (2, 11, 0, 60), 1.1),
    )
    for first, second, timestamp in frames:
        assert reports.update(layout.parse(bytes(first))) is None
        frame = reports.update(layout.parse(bytes(second)))
        assert frame is not None
        tracker.feed(contacts.replace(frame, timestamp), timestamp)

    assert events == [(0, 2, 1.1)]
