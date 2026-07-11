"""Win32 Raw Input and HID transport for Precision Touchpads."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Any, Optional

from cursortrack.backends._touchpad_scroll import ParsedContact, ParsedTouchpadReport

WM_INPUT = 0x00FF
WM_INPUT_DEVICE_CHANGE = 0x00FE
WM_QUIT = 0x0012
RID_INPUT = 0x10000003
RIDI_PREPARSEDDATA = 0x20000005
RIDI_DEVICEINFO = 0x2000000B
RIM_TYPEHID = 2
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
UINT_ERROR = 0xFFFFFFFF
ERROR_INSUFFICIENT_BUFFER = 122

HIDP_INPUT = 0
HIDP_STATUS_SUCCESS = 0x00110000
USAGE_PAGE_GENERIC = 0x01
USAGE_GENERIC_X = 0x30
USAGE_GENERIC_Y = 0x31
USAGE_PAGE_DIGITIZER = 0x0D
USAGE_DIGITIZER_TOUCHPAD = 0x05
USAGE_DIGITIZER_TIP_SWITCH = 0x42
USAGE_DIGITIZER_CONFIDENCE = 0x47
USAGE_DIGITIZER_CONTACT_ID = 0x51
USAGE_DIGITIZER_CONTACT_COUNT = 0x54
USAGE_DIGITIZER_SCAN_TIME = 0x56
MAX_PTP_CONTACTS = 32

_LRESULT = ctypes.c_ssize_t
_WPARAM = ctypes.c_size_t
_LPARAM = ctypes.c_ssize_t
_WNDPROC_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = _WNDPROC_FACTORY(
    _LRESULT,
    ctypes.c_void_p,
    ctypes.c_uint32,
    _WPARAM,
    _LPARAM,
)


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_uint16),
        ("usUsage", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("hwndTarget", ctypes.c_void_p),
    ]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [
        ("hDevice", ctypes.c_void_p),
        ("dwType", ctypes.c_uint32),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("dwSize", ctypes.c_uint32),
        ("hDevice", ctypes.c_void_p),
        ("wParam", _WPARAM),
    ]


class RID_DEVICE_INFO_HID(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [
        ("dwVendorId", ctypes.c_uint32),
        ("dwProductId", ctypes.c_uint32),
        ("dwVersionNumber", ctypes.c_uint32),
        ("usUsagePage", ctypes.c_uint16),
        ("usUsage", ctypes.c_uint16),
    ]


class _RID_DEVICE_INFO_KEYBOARD(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("dwSubType", ctypes.c_uint32),
        ("dwKeyboardMode", ctypes.c_uint32),
        ("dwNumberOfFunctionKeys", ctypes.c_uint32),
        ("dwNumberOfIndicators", ctypes.c_uint32),
        ("dwNumberOfKeysTotal", ctypes.c_uint32),
    ]


class _RID_DEVICE_INFO_UNION(ctypes.Union):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [  # noqa: RUF012 - ctypes requires class-level field schemas
        ("hid", RID_DEVICE_INFO_HID),
        ("keyboard", _RID_DEVICE_INFO_KEYBOARD),
    ]


class RID_DEVICE_INFO(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _anonymous_ = ("value",)
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("dwType", ctypes.c_uint32),
        ("value", _RID_DEVICE_INFO_UNION),
    ]


class HIDP_CAPS(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [
        ("Usage", ctypes.c_uint16),
        ("UsagePage", ctypes.c_uint16),
        ("InputReportByteLength", ctypes.c_uint16),
        ("OutputReportByteLength", ctypes.c_uint16),
        ("FeatureReportByteLength", ctypes.c_uint16),
        ("Reserved", ctypes.c_uint16 * 17),
        ("NumberLinkCollectionNodes", ctypes.c_uint16),
        ("NumberInputButtonCaps", ctypes.c_uint16),
        ("NumberInputValueCaps", ctypes.c_uint16),
        ("NumberInputDataIndices", ctypes.c_uint16),
        ("NumberOutputButtonCaps", ctypes.c_uint16),
        ("NumberOutputValueCaps", ctypes.c_uint16),
        ("NumberOutputDataIndices", ctypes.c_uint16),
        ("NumberFeatureButtonCaps", ctypes.c_uint16),
        ("NumberFeatureValueCaps", ctypes.c_uint16),
        ("NumberFeatureDataIndices", ctypes.c_uint16),
    ]


class _HIDP_VALUE_CAPS_RANGE(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [
        ("UsageMin", ctypes.c_uint16),
        ("UsageMax", ctypes.c_uint16),
        ("StringMin", ctypes.c_uint16),
        ("StringMax", ctypes.c_uint16),
        ("DesignatorMin", ctypes.c_uint16),
        ("DesignatorMax", ctypes.c_uint16),
        ("DataIndexMin", ctypes.c_uint16),
        ("DataIndexMax", ctypes.c_uint16),
    ]


class _HIDP_VALUE_CAPS_NOT_RANGE(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [
        ("Usage", ctypes.c_uint16),
        ("Reserved1", ctypes.c_uint16),
        ("StringIndex", ctypes.c_uint16),
        ("Reserved2", ctypes.c_uint16),
        ("DesignatorIndex", ctypes.c_uint16),
        ("Reserved3", ctypes.c_uint16),
        ("DataIndex", ctypes.c_uint16),
        ("Reserved4", ctypes.c_uint16),
    ]


class _HIDP_VALUE_CAPS_UNION(ctypes.Union):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [  # noqa: RUF012 - ctypes requires class-level field schemas
        ("Range", _HIDP_VALUE_CAPS_RANGE),
        ("NotRange", _HIDP_VALUE_CAPS_NOT_RANGE),
    ]


class _HIDP_BUTTON_CAPS_UNION(ctypes.Union):  # noqa: N801 - mirrors Win32 ABI name
    _fields_ = [  # noqa: RUF012 - ctypes requires class-level field schemas
        ("Range", _HIDP_VALUE_CAPS_RANGE),
        ("NotRange", _HIDP_VALUE_CAPS_NOT_RANGE),
    ]


class HIDP_BUTTON_CAPS(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _anonymous_ = ("value",)
    _fields_ = [
        ("UsagePage", ctypes.c_uint16),
        ("ReportID", ctypes.c_uint8),
        ("IsAlias", ctypes.c_uint8),
        ("BitField", ctypes.c_uint16),
        ("LinkCollection", ctypes.c_uint16),
        ("LinkUsage", ctypes.c_uint16),
        ("LinkUsagePage", ctypes.c_uint16),
        ("IsRange", ctypes.c_uint8),
        ("IsStringRange", ctypes.c_uint8),
        ("IsDesignatorRange", ctypes.c_uint8),
        ("IsAbsolute", ctypes.c_uint8),
        ("ReportCount", ctypes.c_uint16),
        ("Reserved2", ctypes.c_uint16),
        ("Reserved", ctypes.c_uint32 * 9),
        ("value", _HIDP_BUTTON_CAPS_UNION),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):  # noqa: N801 - mirrors Win32 ABI name
    _anonymous_ = ("value",)
    _fields_ = [
        ("UsagePage", ctypes.c_uint16),
        ("ReportID", ctypes.c_uint8),
        ("IsAlias", ctypes.c_uint8),
        ("BitField", ctypes.c_uint16),
        ("LinkCollection", ctypes.c_uint16),
        ("LinkUsage", ctypes.c_uint16),
        ("LinkUsagePage", ctypes.c_uint16),
        ("IsRange", ctypes.c_uint8),
        ("IsStringRange", ctypes.c_uint8),
        ("IsDesignatorRange", ctypes.c_uint8),
        ("IsAbsolute", ctypes.c_uint8),
        ("HasNull", ctypes.c_uint8),
        ("Reserved", ctypes.c_uint8),
        ("BitSize", ctypes.c_uint16),
        ("ReportCount", ctypes.c_uint16),
        ("Reserved2", ctypes.c_uint16 * 5),
        ("UnitsExp", ctypes.c_uint32),
        ("Units", ctypes.c_uint32),
        ("LogicalMin", ctypes.c_int32),
        ("LogicalMax", ctypes.c_int32),
        ("PhysicalMin", ctypes.c_int32),
        ("PhysicalMax", ctypes.c_int32),
        ("value", _HIDP_VALUE_CAPS_UNION),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", _WPARAM),
        ("lParam", _LPARAM),
        ("time", ctypes.c_uint32),
        ("pt", POINT),
        ("lPrivate", ctypes.c_uint32),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("style", ctypes.c_uint32),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int32),
        ("cbWndExtra", ctypes.c_int32),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class RawInputRegistration:
    """One current in-process registration for the touchpad collection."""

    target: int
    flags: int


@dataclass(frozen=True)
class _AxisRange:
    minimum: int
    maximum: int

    def normalize(self, value: int) -> float:
        extent = self.maximum - self.minimum
        if extent <= 0:
            return 0.0
        return max(0.0, min(1.0, (value - self.minimum) / extent))


class _Win32Api:
    """Typed handles for the User32, Kernel32, and HID calls used here."""

    def __init__(self) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("Precision Touchpad raw input is Windows-only.")
        self.user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hid: Any = ctypes.WinDLL("hid", use_last_error=True)
        self._declare_prototypes()

    def _declare_prototypes(self) -> None:
        self.user32.GetRawInputDeviceList.restype = ctypes.c_uint32
        self.user32.GetRawInputDeviceList.argtypes = [
            ctypes.POINTER(RAWINPUTDEVICELIST),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        ]
        self.user32.GetRawInputDeviceInfoW.restype = ctypes.c_uint32
        self.user32.GetRawInputDeviceInfoW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.user32.GetRawInputData.restype = ctypes.c_uint32
        self.user32.GetRawInputData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        ]
        self.user32.GetRegisteredRawInputDevices.restype = ctypes.c_uint32
        self.user32.GetRegisteredRawInputDevices.argtypes = [
            ctypes.POINTER(RAWINPUTDEVICE),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
        ]
        self.user32.RegisterRawInputDevices.restype = ctypes.c_int32
        self.user32.RegisterRawInputDevices.argtypes = [
            ctypes.POINTER(RAWINPUTDEVICE),
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self.user32.RegisterClassExW.restype = ctypes.c_uint16
        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self.user32.UnregisterClassW.restype = ctypes.c_int32
        self.user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        self.user32.CreateWindowExW.restype = ctypes.c_void_p
        self.user32.CreateWindowExW.argtypes = [
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.user32.DestroyWindow.restype = ctypes.c_int32
        self.user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        self.user32.DefWindowProcW.restype = _LRESULT
        self.user32.DefWindowProcW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            _WPARAM,
            _LPARAM,
        ]
        self.user32.GetMessageW.restype = ctypes.c_int32
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self.user32.TranslateMessage.restype = ctypes.c_int32
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.restype = _LRESULT
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.PostThreadMessageW.restype = ctypes.c_int32
        self.user32.PostThreadMessageW.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _WPARAM,
            _LPARAM,
        ]
        self.kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self.kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.hid.HidP_GetCaps.restype = ctypes.c_int32
        self.hid.HidP_GetCaps.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(HIDP_CAPS),
        ]
        self.hid.HidP_GetValueCaps.restype = ctypes.c_int32
        self.hid.HidP_GetValueCaps.argtypes = [
            ctypes.c_int32,
            ctypes.POINTER(HIDP_VALUE_CAPS),
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_void_p,
        ]
        self.hid.HidP_GetButtonCaps.restype = ctypes.c_int32
        self.hid.HidP_GetButtonCaps.argtypes = [
            ctypes.c_int32,
            ctypes.POINTER(HIDP_BUTTON_CAPS),
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_void_p,
        ]
        self.hid.HidP_GetUsageValue.restype = ctypes.c_int32
        self.hid.HidP_GetUsageValue.argtypes = [
            ctypes.c_int32,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.hid.HidP_GetUsages.restype = ctypes.c_int32
        self.hid.HidP_GetUsages.argtypes = [
            ctypes.c_int32,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]

    def touchpad_devices(self) -> tuple[int, ...]:
        for _ in range(3):
            count = ctypes.c_uint32()
            result = self.user32.GetRawInputDeviceList(
                None,
                ctypes.byref(count),
                ctypes.sizeof(RAWINPUTDEVICELIST),
            )
            if result == UINT_ERROR:
                raise OSError(_last_error(), "GetRawInputDeviceList(size) failed")
            if count.value == 0:
                return ()

            devices = (RAWINPUTDEVICELIST * count.value)()
            result = self.user32.GetRawInputDeviceList(
                devices,
                ctypes.byref(count),
                ctypes.sizeof(RAWINPUTDEVICELIST),
            )
            if result != UINT_ERROR:
                return self._filter_touchpads(devices[:result])
            if _last_error() != ERROR_INSUFFICIENT_BUFFER:
                raise OSError(_last_error(), "GetRawInputDeviceList(data) failed")
        raise OSError(ERROR_INSUFFICIENT_BUFFER, "Raw Input device list kept changing")

    def _filter_touchpads(
        self,
        devices: list[RAWINPUTDEVICELIST] | ctypes.Array[Any],
    ) -> tuple[int, ...]:
        matches: list[int] = []
        for device in devices:
            if device.dwType != RIM_TYPEHID:
                continue
            info = RID_DEVICE_INFO()
            info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
            info_size = ctypes.c_uint32(ctypes.sizeof(RID_DEVICE_INFO))
            info_result = self.user32.GetRawInputDeviceInfoW(
                device.hDevice,
                RIDI_DEVICEINFO,
                ctypes.byref(info),
                ctypes.byref(info_size),
            )
            if info_result == UINT_ERROR:
                continue
            if (
                info.hid.usUsagePage == USAGE_PAGE_DIGITIZER
                and info.hid.usUsage == USAGE_DIGITIZER_TOUCHPAD
            ):
                matches.append(int(device.hDevice))
        return tuple(matches)

    def touchpad_registration(self) -> Optional[RawInputRegistration]:
        count = ctypes.c_uint32()
        result = self.user32.GetRegisteredRawInputDevices(
            None,
            ctypes.byref(count),
            ctypes.sizeof(RAWINPUTDEVICE),
        )
        if result == UINT_ERROR:
            raise OSError(_last_error(), "GetRegisteredRawInputDevices(size) failed")
        if count.value == 0:
            return None

        registrations = (RAWINPUTDEVICE * count.value)()
        result = self.user32.GetRegisteredRawInputDevices(
            registrations,
            ctypes.byref(count),
            ctypes.sizeof(RAWINPUTDEVICE),
        )
        if result == UINT_ERROR:
            raise OSError(_last_error(), "GetRegisteredRawInputDevices(data) failed")
        for registration in registrations[:result]:
            if (
                registration.usUsagePage == USAGE_PAGE_DIGITIZER
                and registration.usUsage == USAGE_DIGITIZER_TOUCHPAD
            ):
                return RawInputRegistration(
                    target=int(registration.hwndTarget or 0),
                    flags=int(registration.dwFlags),
                )
        return None

    def register_touchpad(self, window: int) -> None:
        registration = RAWINPUTDEVICE(
            USAGE_PAGE_DIGITIZER,
            USAGE_DIGITIZER_TOUCHPAD,
            RIDEV_INPUTSINK | RIDEV_DEVNOTIFY,
            ctypes.c_void_p(window),
        )
        if not self.user32.RegisterRawInputDevices(
            ctypes.byref(registration),
            1,
            ctypes.sizeof(RAWINPUTDEVICE),
        ):
            raise OSError(_last_error(), "RegisterRawInputDevices failed")

    def remove_touchpad(self) -> None:
        removal = RAWINPUTDEVICE(
            USAGE_PAGE_DIGITIZER,
            USAGE_DIGITIZER_TOUCHPAD,
            RIDEV_REMOVE,
            None,
        )
        if not self.user32.RegisterRawInputDevices(
            ctypes.byref(removal),
            1,
            ctypes.sizeof(RAWINPUTDEVICE),
        ):
            raise OSError(_last_error(), "Raw Input touchpad removal failed")

    def preparsed_data(self, device: int) -> ctypes.Array[Any]:
        size = ctypes.c_uint32()
        result = self.user32.GetRawInputDeviceInfoW(
            ctypes.c_void_p(device),
            RIDI_PREPARSEDDATA,
            None,
            ctypes.byref(size),
        )
        if result == UINT_ERROR or size.value == 0:
            raise OSError(_last_error(), "GetRawInputDeviceInfoW(size) failed")
        data = ctypes.create_string_buffer(size.value)
        result = self.user32.GetRawInputDeviceInfoW(
            ctypes.c_void_p(device),
            RIDI_PREPARSEDDATA,
            data,
            ctypes.byref(size),
        )
        if result == UINT_ERROR:
            raise OSError(_last_error(), "GetRawInputDeviceInfoW(data) failed")
        return data

    def raw_input(self, raw_input_handle: int) -> tuple[int, tuple[bytes, ...]]:
        size = ctypes.c_uint32()
        result = self.user32.GetRawInputData(
            ctypes.c_void_p(raw_input_handle),
            RID_INPUT,
            None,
            ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        if result != 0:
            raise OSError(_last_error(), "GetRawInputData(size) failed")
        if size.value < ctypes.sizeof(RAWINPUTHEADER) + 8:
            raise RuntimeError("Raw Input HID packet is shorter than its header.")

        buffer = ctypes.create_string_buffer(size.value)
        result = self.user32.GetRawInputData(
            ctypes.c_void_p(raw_input_handle),
            RID_INPUT,
            buffer,
            ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        if result == UINT_ERROR or result != size.value:
            raise OSError(_last_error(), "GetRawInputData(data) failed")

        header = ctypes.cast(buffer, ctypes.POINTER(RAWINPUTHEADER)).contents
        if header.dwType != RIM_TYPEHID:
            return 0, ()

        raw = buffer.raw[: size.value]
        offset = ctypes.sizeof(RAWINPUTHEADER)
        report_size = int.from_bytes(raw[offset : offset + 4], "little")
        report_count = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        data_start = offset + 8
        data_size = report_size * report_count
        if report_size <= 0 or report_count <= 0 or data_start + data_size > len(raw):
            raise RuntimeError("Raw Input HID report lengths are invalid.")

        reports = tuple(
            raw[data_start + index * report_size : data_start + (index + 1) * report_size]
            for index in range(report_count)
        )
        return int(header.hDevice or 0), reports


class _HidDeviceLayout:
    """Cached HID descriptor details for one Precision Touchpad."""

    def __init__(self, api: _Win32Api, device: int) -> None:
        self._api = api
        self._preparsed = api.preparsed_data(device)
        caps = HIDP_CAPS()
        status = api.hid.HidP_GetCaps(self._preparsed, ctypes.byref(caps))
        if _unsigned_status(status) != HIDP_STATUS_SUCCESS:
            raise RuntimeError(f"HidP_GetCaps failed with status 0x{_unsigned_status(status):08x}.")

        value_count = ctypes.c_uint16(caps.NumberInputValueCaps)
        if value_count.value == 0:
            raise RuntimeError("Touchpad HID descriptor has no input value capabilities.")
        value_caps = (HIDP_VALUE_CAPS * value_count.value)()
        status = api.hid.HidP_GetValueCaps(
            HIDP_INPUT,
            value_caps,
            ctypes.byref(value_count),
            self._preparsed,
        )
        if _unsigned_status(status) != HIDP_STATUS_SUCCESS:
            raise RuntimeError(
                f"HidP_GetValueCaps failed with status 0x{_unsigned_status(status):08x}."
            )

        button_count = ctypes.c_uint16(caps.NumberInputButtonCaps)
        if button_count.value == 0:
            raise RuntimeError("Touchpad HID descriptor has no input button capabilities.")
        button_caps = (HIDP_BUTTON_CAPS * button_count.value)()
        status = api.hid.HidP_GetButtonCaps(
            HIDP_INPUT,
            button_caps,
            ctypes.byref(button_count),
            self._preparsed,
        )
        if _unsigned_status(status) != HIDP_STATUS_SUCCESS:
            raise RuntimeError(
                f"HidP_GetButtonCaps failed with status 0x{_unsigned_status(status):08x}."
            )

        self._axes: dict[int, tuple[_AxisRange, _AxisRange]] = {}
        self._report_links: dict[int, int] = {}
        by_link: dict[int, dict[int, _AxisRange]] = {}
        contact_id_links: set[int] = set()
        for capability in value_caps[: value_count.value]:
            if capability.UsagePage == USAGE_PAGE_GENERIC:
                for usage in (USAGE_GENERIC_X, USAGE_GENERIC_Y):
                    if _capability_contains(capability, usage):
                        by_link.setdefault(capability.LinkCollection, {})[usage] = _AxisRange(
                            capability.LogicalMin,
                            capability.LogicalMax,
                        )
            elif capability.UsagePage == USAGE_PAGE_DIGITIZER:
                if _capability_contains(capability, USAGE_DIGITIZER_CONTACT_ID):
                    contact_id_links.add(int(capability.LinkCollection))
                for usage in (
                    USAGE_DIGITIZER_CONTACT_COUNT,
                    USAGE_DIGITIZER_SCAN_TIME,
                ):
                    if _capability_contains(capability, usage):
                        self._report_links[usage] = capability.LinkCollection

        button_usages: dict[int, set[int]] = {}
        for button_capability in button_caps[: button_count.value]:
            if button_capability.UsagePage != USAGE_PAGE_DIGITIZER:
                continue
            for usage in (
                USAGE_DIGITIZER_TIP_SWITCH,
                USAGE_DIGITIZER_CONFIDENCE,
            ):
                if _button_capability_contains(button_capability, usage):
                    button_usages.setdefault(int(button_capability.LinkCollection), set()).add(
                        usage
                    )

        incomplete_links: list[int] = []
        for link, axes in by_link.items():
            if USAGE_GENERIC_X in axes and USAGE_GENERIC_Y in axes:
                required_buttons = {
                    USAGE_DIGITIZER_TIP_SWITCH,
                    USAGE_DIGITIZER_CONFIDENCE,
                }
                if link not in contact_id_links or not required_buttons <= button_usages.get(
                    link, set()
                ):
                    incomplete_links.append(link)
                    continue
                self._axes[link] = (axes[USAGE_GENERIC_X], axes[USAGE_GENERIC_Y])
        if incomplete_links:
            links = ", ".join(str(link) for link in sorted(incomplete_links))
            raise RuntimeError(
                "Touchpad HID contact collections are missing mandatory Contact ID, "
                f"Tip, or Confidence usages (link collections: {links})."
            )
        if not self._axes:
            raise RuntimeError("Touchpad HID descriptor exposes no complete contact collections.")
        missing_report_usages = {
            USAGE_DIGITIZER_CONTACT_COUNT,
            USAGE_DIGITIZER_SCAN_TIME,
        } - self._report_links.keys()
        if missing_report_usages:
            missing = ", ".join(f"0x{usage:02x}" for usage in sorted(missing_report_usages))
            raise RuntimeError(f"Touchpad HID descriptor is missing mandatory usages: {missing}.")

    def parse(self, report: bytes) -> ParsedTouchpadReport:
        if not report:
            raise ValueError("Touchpad HID report is empty.")
        report_buffer = ctypes.create_string_buffer(report, len(report))
        contact_count = self._usage_value(
            USAGE_PAGE_DIGITIZER,
            self._report_links[USAGE_DIGITIZER_CONTACT_COUNT],
            USAGE_DIGITIZER_CONTACT_COUNT,
            report_buffer,
        )
        scan_time = self._usage_value(
            USAGE_PAGE_DIGITIZER,
            self._report_links[USAGE_DIGITIZER_SCAN_TIME],
            USAGE_DIGITIZER_SCAN_TIME,
            report_buffer,
        )
        if contact_count is None or scan_time is None:
            raise RuntimeError("Touchpad report lacks mandatory contact-count or scan-time data.")
        if contact_count > MAX_PTP_CONTACTS:
            raise RuntimeError(
                f"Touchpad report declares {contact_count} contacts; "
                f"the safety limit is {MAX_PTP_CONTACTS}."
            )

        contacts: list[ParsedContact] = []
        for link, (x_range, y_range) in sorted(self._axes.items()):
            x = self._usage_value(USAGE_PAGE_GENERIC, link, USAGE_GENERIC_X, report_buffer)
            y = self._usage_value(USAGE_PAGE_GENERIC, link, USAGE_GENERIC_Y, report_buffer)
            contact_id = self._usage_value(
                USAGE_PAGE_DIGITIZER,
                link,
                USAGE_DIGITIZER_CONTACT_ID,
                report_buffer,
            )
            if x is None or y is None or contact_id is None:
                continue
            button_usages = self._digitizer_usages(link, report_buffer)
            if button_usages is None:
                continue
            contacts.append(
                ParsedContact(
                    contact_id=contact_id,
                    x=x_range.normalize(x),
                    y=y_range.normalize(y),
                    touching=USAGE_DIGITIZER_TIP_SWITCH in button_usages,
                    confident=USAGE_DIGITIZER_CONFIDENCE in button_usages,
                )
            )
        return ParsedTouchpadReport(
            scan_time=scan_time,
            contact_count=contact_count,
            contacts=tuple(contacts),
        )

    def _usage_value(
        self,
        usage_page: int,
        link: int,
        usage: int,
        report: ctypes.Array[Any],
    ) -> Optional[int]:
        value = ctypes.c_uint32()
        status = self._api.hid.HidP_GetUsageValue(
            HIDP_INPUT,
            usage_page,
            link,
            usage,
            ctypes.byref(value),
            self._preparsed,
            report,
            len(report),
        )
        if _unsigned_status(status) != HIDP_STATUS_SUCCESS:
            return None
        return int(value.value)

    def _digitizer_usages(
        self,
        link: int,
        report: ctypes.Array[Any],
    ) -> Optional[frozenset[int]]:
        usages = (ctypes.c_uint16 * 16)()
        usage_count = ctypes.c_uint32(len(usages))
        status = self._api.hid.HidP_GetUsages(
            HIDP_INPUT,
            USAGE_PAGE_DIGITIZER,
            link,
            usages,
            ctypes.byref(usage_count),
            self._preparsed,
            report,
            len(report),
        )
        if _unsigned_status(status) != HIDP_STATUS_SUCCESS:
            return None
        return frozenset(int(usage) for usage in usages[: usage_count.value])


def _capability_contains(capability: HIDP_VALUE_CAPS, usage: int) -> bool:
    if capability.IsRange:
        minimum = int(capability.Range.UsageMin)
        maximum = int(capability.Range.UsageMax)
        return minimum <= usage <= maximum
    return int(capability.NotRange.Usage) == usage


def _button_capability_contains(capability: HIDP_BUTTON_CAPS, usage: int) -> bool:
    if capability.IsRange:
        minimum = int(capability.Range.UsageMin)
        maximum = int(capability.Range.UsageMax)
        return minimum <= usage <= maximum
    return int(capability.NotRange.Usage) == usage


def _unsigned_status(status: int) -> int:
    return status & UINT_ERROR


def _last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0
