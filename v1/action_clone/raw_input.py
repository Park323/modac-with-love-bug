from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from datetime import datetime, timezone
from typing import Any


user32 = ctypes.windll.user32
HICON = wintypes.HANDLE
HCURSOR = wintypes.HANDLE
HBRUSH = wintypes.HANDLE

WM_INPUT = 0x00FF
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
PM_REMOVE = 0x0001

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_KEY_BREAK = 0x0001

VK_TO_KEY = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x10: "Shift",
    0x11: "Ctrl",
    0x12: "Alt",
    0x1B: "Esc",
    0x20: "Space",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x30: "0",
    0x31: "1",
    0x32: "2",
    0x33: "3",
    0x34: "4",
    0x35: "5",
    0x36: "6",
    0x37: "7",
    0x38: "8",
    0x39: "9",
    0x41: "A",
    0x42: "B",
    0x43: "C",
    0x44: "D",
    0x45: "E",
    0x46: "F",
    0x47: "G",
    0x48: "H",
    0x49: "I",
    0x4A: "J",
    0x4B: "K",
    0x4C: "L",
    0x4D: "M",
    0x4E: "N",
    0x4F: "O",
    0x50: "P",
    0x51: "Q",
    0x52: "R",
    0x53: "S",
    0x54: "T",
    0x55: "U",
    0x56: "V",
    0x57: "W",
    0x58: "X",
    0x59: "Y",
    0x5A: "Z",
    0x70: "F1",
    0x71: "F2",
    0x72: "F3",
    0x73: "F4",
    0x74: "F5",
    0x75: "F6",
    0x76: "F7",
    0x77: "F8",
    0x78: "F9",
    0x79: "F10",
    0x7A: "F11",
    0x7B: "F12",
}


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWMOUSE_BUTTONS_STRUCT(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class RAWMOUSE_BUTTONS(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("buttons", RAWMOUSE_BUTTONS_STRUCT),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("buttons", RAWMOUSE_BUTTONS),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWINPUTUNION(ctypes.Union):
    _fields_ = [
        ("mouse", RAWMOUSE),
        ("keyboard", RAWKEYBOARD),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", RAWINPUTUNION),
    ]


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


WndProcType = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class RawInputRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        window_title: str,
        mouse_sample_hz: float,
    ) -> None:
        self.session_id = session_id
        self.window_title = window_title
        self.mouse_sample_hz = mouse_sample_hz
        self.events: list[dict[str, Any]] = []
        self._pressed_keys: set[str] = set()
        self._t0 = 0.0
        self._hwnd: int | None = None
        self._wnd_proc = WndProcType(self._window_proc)

    def run(self, duration_sec: float | None) -> dict[str, Any]:
        if duration_sec is None:
            raise ValueError("Raw backend requires --duration because fullscreen may block stop hotkeys.")

        self._t0 = time.perf_counter()
        self._create_window()
        self._register_raw_input()

        deadline = self._t0 + duration_sec
        msg = MSG()
        try:
            while time.perf_counter() < deadline:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.001)
        finally:
            self._release_any_pressed_keys()
            if self._hwnd:
                user32.DestroyWindow(self._hwnd)

        duration = round(time.perf_counter() - self._t0, 6)
        return {
            "schema_version": "0.1",
            "session": {
                "session_id": self.session_id,
                "game": "CrossFire",
                "mode": "Team Deathmatch",
                "map": "Transport Ship 2.0",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "duration_sec": duration,
                "window_title": self.window_title,
            },
            "environment": {
                "backend": "raw",
                "mouse_move_format": "raw_relative_delta",
            },
            "events": sorted(self.events, key=lambda event: event["t"]),
        }

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 6)

    def _create_window(self) -> None:
        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        class_name = "ModacthonRawInputWindow"
        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = self._wnd_proc
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = class_name
        user32.RegisterClassW(ctypes.byref(wndclass))
        self._hwnd = user32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, None, None, hinstance, None)
        if not self._hwnd:
            raise ctypes.WinError()

    def _register_raw_input(self) -> None:
        devices = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, self._hwnd),
            RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self._hwnd),
        )
        ok = user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE))
        if not ok:
            raise ctypes.WinError()

    def _window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_INPUT:
            self._handle_raw_input(lparam)
            return 0
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_raw_input(self, lparam: int) -> None:
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size)
        if size.value == 0:
            return
        buffer = ctypes.create_string_buffer(size.value)
        read = user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(size), header_size)
        if read != size.value:
            return

        raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType == RIM_TYPEKEYBOARD:
            self._handle_keyboard(raw.data.keyboard)
        elif raw.header.dwType == RIM_TYPEMOUSE:
            self._handle_mouse(raw.data.mouse)

    def _handle_keyboard(self, keyboard: RAWKEYBOARD) -> None:
        key_name = VK_TO_KEY.get(keyboard.VKey)
        if not key_name:
            return
        if key_name in {"F8", "F9"}:
            return

        is_key_up = bool(keyboard.Flags & RI_KEY_BREAK)
        if is_key_up:
            if key_name in self._pressed_keys:
                self.events.append({"t": self._elapsed(), "type": "key_up", "key": key_name})
                self._pressed_keys.discard(key_name)
            return

        if key_name not in self._pressed_keys:
            self.events.append({"t": self._elapsed(), "type": "key_down", "key": key_name})
            self._pressed_keys.add(key_name)

    def _handle_mouse(self, mouse: RAWMOUSE) -> None:
        if mouse.lLastX or mouse.lLastY:
            self.events.append({"t": self._elapsed(), "type": "mouse_move", "dx": mouse.lLastX, "dy": mouse.lLastY})

        flags = mouse.buttons.buttons.usButtonFlags
        if flags & RI_MOUSE_LEFT_BUTTON_DOWN:
            self.events.append({"t": self._elapsed(), "type": "mouse_button_down", "button": "left"})
        if flags & RI_MOUSE_LEFT_BUTTON_UP:
            self.events.append({"t": self._elapsed(), "type": "mouse_button_up", "button": "left"})
        if flags & RI_MOUSE_RIGHT_BUTTON_DOWN:
            self.events.append({"t": self._elapsed(), "type": "mouse_button_down", "button": "right"})
        if flags & RI_MOUSE_RIGHT_BUTTON_UP:
            self.events.append({"t": self._elapsed(), "type": "mouse_button_up", "button": "right"})
        if flags & RI_MOUSE_MIDDLE_BUTTON_DOWN:
            self.events.append({"t": self._elapsed(), "type": "mouse_button_down", "button": "middle"})
        if flags & RI_MOUSE_MIDDLE_BUTTON_UP:
            self.events.append({"t": self._elapsed(), "type": "mouse_button_up", "button": "middle"})

    def _release_any_pressed_keys(self) -> None:
        for key_name in sorted(self._pressed_keys):
            self.events.append({"t": self._elapsed(), "type": "key_up", "key": key_name})
        self._pressed_keys.clear()
