#!/usr/bin/env python3
"""
Record and replay keyboard/mouse scenarios for Windows game QA.

No third-party packages are required. Run with Python 3 on Windows.
Stop recording with Ctrl+Shift+F12.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time
from pathlib import Path


if os.name != "nt":
    raise SystemExit("This tool only runs on Windows.")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E
WM_INPUT = 0x00FF

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_F12 = 0x7B
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIDEV_INPUTSINK = 0x00000100
RIDEV_NOLEGACY = 0x00000030
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02
HID_USAGE_GENERIC_KEYBOARD = 0x06
RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800
RI_KEY_BREAK = 0x0001
RI_KEY_E0 = 0x0002
LLKHF_EXTENDED = 0x01
MAPVK_VK_TO_VSC = 0
KEYBOARD_VKS = [
    vk
    for vk in range(1, 256)
    if vk not in {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2}
]
MOUSE_BUTTONS = {
    VK_LBUTTON: ("left_down", "left_up", 0),
    VK_RBUTTON: ("right_down", "right_up", 0),
    VK_MBUTTON: ("middle_down", "middle_up", 0),
    VK_XBUTTON1: ("x_down", "x_up", 1),
    VK_XBUTTON2: ("x_down", "x_up", 2),
}
EXTENDED_VKS = {
    VK_RCONTROL,
    VK_RMENU,
    VK_INSERT,
    VK_DELETE,
    VK_HOME,
    VK_END,
    VK_PRIOR,
    VK_NEXT,
    VK_LEFT,
    VK_UP,
    VK_RIGHT,
    VK_DOWN,
}
STOP_HOTKEY_VKS = {
    VK_CONTROL,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_SHIFT,
    VK_LSHIFT,
    VK_RSHIFT,
    VK_F12,
}
CTRL_VKS = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
SHIFT_VKS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}

ULONG_PTR = wintypes.WPARAM
LRESULT = wintypes.LPARAM
LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
LowLevelMouseProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
WndProc = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


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


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWMOUSE_BUTTON_DATA(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class RAWMOUSE_BUTTONS(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("button_data", RAWMOUSE_BUTTON_DATA),
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


class RAWHID(ctypes.Structure):
    _fields_ = [
        ("dwSizeHid", wintypes.DWORD),
        ("dwCount", wintypes.DWORD),
        ("bRawData", wintypes.BYTE * 1),
    ]


class RAWINPUTDATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD), ("hid", RAWHID)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", RAWINPUTDATA)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WndProc),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE),
    wintypes.UINT,
    wintypes.UINT,
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM if hasattr(wintypes, "ATOM") else wintypes.WORD
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def signed_word(value: int) -> int:
    word = (value >> 16) & 0xFFFF
    return word - 0x10000 if word & 0x8000 else word


def high_word(value: int) -> int:
    return (value >> 16) & 0xFFFF


def now() -> float:
    return time.perf_counter()


def is_pressed(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor_position() -> tuple[int, int]:
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return int(point.x), int(point.y)


def scan_code_for_vk(vk: int) -> int:
    scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    if vk in {VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN}:
        return scan or {
            VK_LEFT: 0x4B,
            VK_UP: 0x48,
            VK_RIGHT: 0x4D,
            VK_DOWN: 0x50,
        }[vk]
    return scan


def format_event(event: dict) -> str:
    prefix = f"{event['t']:>9.3f}s"
    if event["kind"] == "keyboard":
        return (
            f"{prefix} keyboard {event['action']:<4} "
            f"vk={event['vk']:<3} scan={event['scan']:<3} "
            f"extended={str(event.get('extended', False)).lower()}"
        )

    parts = [
        prefix,
        "mouse",
        f"{event['action']:<10}",
    ]
    if event["action"] == "raw_move":
        parts.extend([f"dx={event['dx']}", f"dy={event['dy']}"])
    else:
        parts.extend([f"x={event['x']}", f"y={event['y']}"])
    if "delta" in event:
        parts.append(f"delta={event['delta']}")
    if "button" in event:
        parts.append(f"button={event['button']}")
    return " ".join(parts)


class Recorder:
    def __init__(self, output: Path, move_interval: float, live: bool) -> None:
        self.output = output
        self.move_interval = move_interval
        self.live = live
        self.events: list[dict] = []
        self.start = now()
        self.last_move_time = 0.0
        self.last_move_pos: tuple[int, int] | None = None
        self.pressed: set[int] = set()
        self.keyboard_hook = None
        self.mouse_hook = None
        self.keyboard_proc = LowLevelKeyboardProc(self._keyboard_callback)
        self.mouse_proc = LowLevelMouseProc(self._mouse_callback)

    def elapsed(self) -> float:
        return round(now() - self.start, 6)

    def record_event(self, event: dict) -> None:
        self.events.append(event)
        if self.live:
            print(format_event(event), flush=True)

    def run(self) -> None:
        print("Recording. Stop with Ctrl+Shift+F12.")
        self.start = now()
        module = kernel32.GetModuleHandleW(None)
        self.keyboard_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self.keyboard_proc, module, 0
        )
        if not self.keyboard_hook:
            raise ctypes.WinError()
        self.mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self.mouse_proc, module, 0
        )
        if not self.mouse_hook:
            user32.UnhookWindowsHookEx(self.keyboard_hook)
            self.keyboard_hook = None
            raise ctypes.WinError()

        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.keyboard_hook:
                user32.UnhookWindowsHookEx(self.keyboard_hook)
            if self.mouse_hook:
                user32.UnhookWindowsHookEx(self.mouse_hook)
            self.save()

    def stop_requested(self, vk: int) -> bool:
        return (
            vk == VK_F12
            and any(ctrl in self.pressed for ctrl in CTRL_VKS)
            and any(shift in self.pressed for shift in SHIFT_VKS)
        )

    def remove_stop_hotkey_tail(self) -> None:
        while self.events:
            event = self.events[-1]
            if event.get("kind") != "keyboard" or event.get("vk") not in STOP_HOTKEY_VKS:
                break
            self.events.pop()

    def save(self) -> None:
        self.remove_stop_hotkey_tail()
        payload = {
            "version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "capture_method": "hook",
            "stop_hotkey": "Ctrl+Shift+F12",
            "events": self.events,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved {len(self.events)} events to {self.output}")

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(data.vkCode)
            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)

            if is_down:
                self.pressed.add(vk)
                if self.stop_requested(vk):
                    user32.PostQuitMessage(0)
                    return 1
            elif is_up:
                self.pressed.discard(vk)

            if is_down or is_up:
                self.record_event(
                    {
                        "t": self.elapsed(),
                        "kind": "keyboard",
                        "action": "down" if is_down else "up",
                        "vk": vk,
                        "scan": int(data.scanCode),
                        "extended": bool(data.flags & LLKHF_EXTENDED),
                    }
                )
        return user32.CallNextHookEx(self.keyboard_hook, n_code, w_param, l_param)

    def _mouse_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x, y = int(data.pt.x), int(data.pt.y)
            event_time = self.elapsed()
            action = {
                WM_MOUSEMOVE: "move",
                WM_LBUTTONDOWN: "left_down",
                WM_LBUTTONUP: "left_up",
                WM_RBUTTONDOWN: "right_down",
                WM_RBUTTONUP: "right_up",
                WM_MBUTTONDOWN: "middle_down",
                WM_MBUTTONUP: "middle_up",
                WM_MOUSEWHEEL: "wheel",
                WM_MOUSEHWHEEL: "hwheel",
                WM_XBUTTONDOWN: "x_down",
                WM_XBUTTONUP: "x_up",
            }.get(w_param)

            if action == "move":
                too_soon = event_time - self.last_move_time < self.move_interval
                same_pos = self.last_move_pos == (x, y)
                if too_soon or same_pos:
                    return user32.CallNextHookEx(
                        self.mouse_hook, n_code, w_param, l_param
                    )
                self.last_move_time = event_time
                self.last_move_pos = (x, y)

            if action:
                event = {
                    "t": event_time,
                    "kind": "mouse",
                    "action": action,
                    "x": x,
                    "y": y,
                }
                if action in ("wheel", "hwheel"):
                    event["delta"] = signed_word(int(data.mouseData))
                if action in ("x_down", "x_up"):
                    event["button"] = high_word(int(data.mouseData))
                self.record_event(event)
        return user32.CallNextHookEx(self.mouse_hook, n_code, w_param, l_param)


class PollingRecorder:
    def __init__(
        self, output: Path, move_interval: float, poll_interval: float, live: bool
    ) -> None:
        self.output = output
        self.move_interval = move_interval
        self.poll_interval = poll_interval
        self.live = live
        self.events: list[dict] = []
        self.start = now()
        self.last_move_time = 0.0
        self.last_move_pos: tuple[int, int] | None = None
        self.key_states: dict[int, bool] = {}
        self.button_states: dict[int, bool] = {}

    def elapsed(self) -> float:
        return round(now() - self.start, 6)

    def record_event(self, event: dict) -> None:
        self.events.append(event)
        if self.live:
            print(format_event(event), flush=True)

    def stop_requested(self) -> bool:
        return (
            is_pressed(VK_F12)
            and any(is_pressed(ctrl) for ctrl in CTRL_VKS)
            and any(is_pressed(shift) for shift in SHIFT_VKS)
        )

    def remove_stop_hotkey_tail(self) -> None:
        while self.events:
            event = self.events[-1]
            if event.get("kind") != "keyboard" or event.get("vk") not in STOP_HOTKEY_VKS:
                break
            self.events.pop()

    def save(self) -> None:
        self.remove_stop_hotkey_tail()
        payload = {
            "version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "capture_method": "poll",
            "stop_hotkey": "Ctrl+Shift+F12",
            "events": self.events,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved {len(self.events)} events to {self.output}")

    def run(self) -> None:
        print("Recording with polling. Stop with Ctrl+Shift+F12.")
        self.start = now()
        self.key_states = {vk: is_pressed(vk) for vk in KEYBOARD_VKS}
        self.button_states = {vk: is_pressed(vk) for vk in MOUSE_BUTTONS}
        self.last_move_pos = cursor_position()
        self.last_move_time = self.elapsed()

        try:
            while not self.stop_requested():
                self.capture_keyboard()
                self.capture_mouse()
                time.sleep(self.poll_interval)
        finally:
            self.save()

    def capture_keyboard(self) -> None:
        event_time = self.elapsed()
        for vk in KEYBOARD_VKS:
            pressed = is_pressed(vk)
            if pressed == self.key_states.get(vk, False):
                continue
            self.key_states[vk] = pressed
            scan = scan_code_for_vk(vk)
            if scan == 0:
                continue
            self.record_event(
                {
                    "t": event_time,
                    "kind": "keyboard",
                    "action": "down" if pressed else "up",
                    "vk": vk,
                    "scan": scan,
                    "extended": vk in EXTENDED_VKS,
                }
            )

    def capture_mouse(self) -> None:
        x, y = cursor_position()
        event_time = self.elapsed()
        moved = self.last_move_pos != (x, y)
        move_due = event_time - self.last_move_time >= self.move_interval
        if moved and move_due:
            self.last_move_pos = (x, y)
            self.last_move_time = event_time
            self.record_event(
                {
                    "t": event_time,
                    "kind": "mouse",
                    "action": "move",
                    "x": x,
                    "y": y,
                }
            )

        for vk, (down_action, up_action, button) in MOUSE_BUTTONS.items():
            pressed = is_pressed(vk)
            if pressed == self.button_states.get(vk, False):
                continue
            self.button_states[vk] = pressed
            event = {
                "t": event_time,
                "kind": "mouse",
                "action": down_action if pressed else up_action,
                "x": x,
                "y": y,
            }
            if button:
                event["button"] = button
            self.record_event(event)


class RawInputRecorder:
    def __init__(self, output: Path, move_interval: float, live: bool) -> None:
        self.output = output
        self.move_interval = move_interval
        self.live = live
        self.events: list[dict] = []
        self.start = now()
        self.last_move_time = 0.0
        self.pressed: set[int] = set()
        self.hwnd = None
        self.instance = kernel32.GetModuleHandleW(None)
        self.class_name = f"GameQACloneRawInput{os.getpid()}"
        self.wnd_proc = WndProc(self._wnd_proc)

    def elapsed(self) -> float:
        return round(now() - self.start, 6)

    def record_event(self, event: dict) -> None:
        self.events.append(event)
        if self.live:
            print(format_event(event), flush=True)

    def stop_requested(self, vk: int) -> bool:
        return (
            vk == VK_F12
            and any(ctrl in self.pressed for ctrl in CTRL_VKS)
            and any(shift in self.pressed for shift in SHIFT_VKS)
        )

    def remove_stop_hotkey_tail(self) -> None:
        while self.events:
            event = self.events[-1]
            if event.get("kind") != "keyboard" or event.get("vk") not in STOP_HOTKEY_VKS:
                break
            self.events.pop()

    def save(self) -> None:
        self.remove_stop_hotkey_tail()
        payload = {
            "version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "capture_method": "raw",
            "stop_hotkey": "Ctrl+Shift+F12",
            "events": self.events,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved {len(self.events)} events to {self.output}")

    def run(self) -> None:
        print("Recording with Raw Input. Stop with Ctrl+Shift+F12.")
        self.start = now()
        self.create_window()
        self.register_devices()

        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
            self.save()

    def create_window(self) -> None:
        wnd_class = WNDCLASSW(
            style=0,
            lpfnWndProc=self.wnd_proc,
            cbClsExtra=0,
            cbWndExtra=0,
            hInstance=self.instance,
            hIcon=None,
            hCursor=None,
            hbrBackground=None,
            lpszMenuName=None,
            lpszClassName=self.class_name,
        )
        if not user32.RegisterClassW(ctypes.byref(wnd_class)):
            raise ctypes.WinError()
        self.hwnd = user32.CreateWindowExW(
            0,
            self.class_name,
            self.class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            self.instance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError()

    def register_devices(self) -> None:
        devices = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(
                usUsagePage=HID_USAGE_PAGE_GENERIC,
                usUsage=HID_USAGE_GENERIC_MOUSE,
                dwFlags=RIDEV_INPUTSINK,
                hwndTarget=self.hwnd,
            ),
            RAWINPUTDEVICE(
                usUsagePage=HID_USAGE_PAGE_GENERIC,
                usUsage=HID_USAGE_GENERIC_KEYBOARD,
                dwFlags=RIDEV_INPUTSINK,
                hwndTarget=self.hwnd,
            ),
        )
        if not user32.RegisterRawInputDevices(
            devices, len(devices), ctypes.sizeof(RAWINPUTDEVICE)
        ):
            raise ctypes.WinError()

    def read_raw_input(self, l_param: int) -> RAWINPUT | None:
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        user32.GetRawInputData(l_param, RID_INPUT, None, ctypes.byref(size), header_size)
        if size.value == 0:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        result = user32.GetRawInputData(
            l_param, RID_INPUT, buffer, ctypes.byref(size), header_size
        )
        if result == ctypes.c_uint(-1).value:
            return None
        return ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents

    def _wnd_proc(self, hwnd: int, msg: int, w_param: int, l_param: int) -> int:
        if msg == WM_INPUT:
            raw = self.read_raw_input(l_param)
            if raw:
                if raw.header.dwType == RIM_TYPEKEYBOARD:
                    self.capture_keyboard(raw.data.keyboard)
                elif raw.header.dwType == RIM_TYPEMOUSE:
                    self.capture_mouse(raw.data.mouse)
        return user32.DefWindowProcW(hwnd, msg, w_param, l_param)

    def capture_keyboard(self, keyboard: RAWKEYBOARD) -> None:
        vk = int(keyboard.VKey)
        if vk == 0xFF:
            return
        is_up = bool(keyboard.Flags & RI_KEY_BREAK)
        is_down = not is_up

        if is_down:
            if vk in self.pressed:
                return
            self.pressed.add(vk)
            if self.stop_requested(vk):
                user32.PostQuitMessage(0)
                return
        else:
            if vk not in self.pressed:
                return
            self.pressed.discard(vk)

        scan = int(keyboard.MakeCode) or scan_code_for_vk(vk)
        if scan == 0:
            return
        self.record_event(
            {
                "t": self.elapsed(),
                "kind": "keyboard",
                "action": "down" if is_down else "up",
                "vk": vk,
                "scan": scan,
                "extended": bool(keyboard.Flags & RI_KEY_E0) or vk in EXTENDED_VKS,
            }
        )

    def capture_mouse(self, mouse: RAWMOUSE) -> None:
        event_time = self.elapsed()
        dx, dy = int(mouse.lLastX), int(mouse.lLastY)
        if dx or dy:
            if event_time - self.last_move_time >= self.move_interval:
                self.last_move_time = event_time
                self.record_event(
                    {
                        "t": event_time,
                        "kind": "mouse",
                        "action": "raw_move",
                        "dx": dx,
                        "dy": dy,
                    }
                )

        flags = int(mouse.buttons.button_data.usButtonFlags)
        data = signed_word(int(mouse.buttons.button_data.usButtonData) << 16)
        button_events = [
            (RI_MOUSE_LEFT_BUTTON_DOWN, "left_down", 0),
            (RI_MOUSE_LEFT_BUTTON_UP, "left_up", 0),
            (RI_MOUSE_RIGHT_BUTTON_DOWN, "right_down", 0),
            (RI_MOUSE_RIGHT_BUTTON_UP, "right_up", 0),
            (RI_MOUSE_MIDDLE_BUTTON_DOWN, "middle_down", 0),
            (RI_MOUSE_MIDDLE_BUTTON_UP, "middle_up", 0),
            (RI_MOUSE_BUTTON_4_DOWN, "x_down", 1),
            (RI_MOUSE_BUTTON_4_UP, "x_up", 1),
            (RI_MOUSE_BUTTON_5_DOWN, "x_down", 2),
            (RI_MOUSE_BUTTON_5_UP, "x_up", 2),
        ]
        x, y = cursor_position()
        for flag, action, button in button_events:
            if flags & flag:
                event = {
                    "t": event_time,
                    "kind": "mouse",
                    "action": action,
                    "x": x,
                    "y": y,
                }
                if button:
                    event["button"] = button
                self.record_event(event)
        if flags & RI_MOUSE_WHEEL:
            self.record_event(
                {
                    "t": event_time,
                    "kind": "mouse",
                    "action": "wheel",
                    "x": x,
                    "y": y,
                    "delta": data,
                }
            )
        if flags & RI_MOUSE_HWHEEL:
            self.record_event(
                {
                    "t": event_time,
                    "kind": "mouse",
                    "action": "hwheel",
                    "x": x,
                    "y": y,
                    "delta": data,
                }
            )


def normalize_position(x: int, y: int) -> tuple[int, int]:
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
    height = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
    return (
        int((x - left) * 65535 / width),
        int((y - top) * 65535 / height),
    )


def send_mouse(x: int, y: int, flags: int, mouse_data: int = 0) -> None:
    nx, ny = normalize_position(x, y)
    input_data = INPUT(
        type=INPUT_MOUSE,
        union=INPUTUNION(
            mi=MOUSEINPUT(
                dx=nx,
                dy=ny,
                mouseData=mouse_data,
                dwFlags=flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    if user32.SendInput(1, ctypes.byref(input_data), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def send_mouse_relative(dx: int, dy: int) -> None:
    input_data = INPUT(
        type=INPUT_MOUSE,
        union=INPUTUNION(
            mi=MOUSEINPUT(
                dx=dx,
                dy=dy,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    if user32.SendInput(1, ctypes.byref(input_data), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def send_keyboard(scan: int, extended: bool, is_up: bool) -> None:
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if is_up:
        flags |= KEYEVENTF_KEYUP
    input_data = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUTUNION(
            ki=KEYBDINPUT(
                wVk=0,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    if user32.SendInput(1, ctypes.byref(input_data), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def play(path: Path, speed: float, countdown: float) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    if not events:
        raise SystemExit(f"No events found in {path}")

    print(f"Replaying {len(events)} events from {path}")
    if countdown > 0:
        print(f"Starting in {countdown:g} seconds. Focus the game window now.")
        time.sleep(countdown)

    last_t = 0.0
    for event in events:
        event_t = float(event["t"])
        delay = max(0.0, (event_t - last_t) / speed)
        if delay:
            time.sleep(delay)
        last_t = event_t

        if event["kind"] == "keyboard":
            send_keyboard(
                scan=int(event["scan"]),
                extended=bool(event.get("extended", False)),
                is_up=event["action"] == "up",
            )
        elif event["kind"] == "mouse":
            action = event["action"]
            if action == "raw_move":
                send_mouse_relative(int(event["dx"]), int(event["dy"]))
                continue
            x, y = int(event["x"]), int(event["y"])
            flags = {
                "move": MOUSEEVENTF_MOVE,
                "left_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN,
                "left_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP,
                "right_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTDOWN,
                "right_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTUP,
                "middle_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_MIDDLEDOWN,
                "middle_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_MIDDLEUP,
                "wheel": MOUSEEVENTF_MOVE | MOUSEEVENTF_WHEEL,
                "hwheel": MOUSEEVENTF_MOVE | MOUSEEVENTF_HWHEEL,
                "x_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_XDOWN,
                "x_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_XUP,
            }.get(action)
            if flags is None:
                continue
            mouse_data = int(event.get("delta", event.get("button", 0)))
            send_mouse(x, y, flags, mouse_data)
    print("Replay complete.")


def inspect(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    duration = events[-1]["t"] if events else 0
    keyboard = sum(1 for event in events if event.get("kind") == "keyboard")
    mouse = sum(1 for event in events if event.get("kind") == "mouse")
    print(f"File: {path}")
    print(f"Events: {len(events)}")
    print(f"Duration: {duration:.3f}s")
    print(f"Keyboard: {keyboard}")
    print(f"Mouse: {mouse}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record and replay Windows keyboard/mouse scenarios for game QA."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    record_parser = sub.add_parser("record", help="record a scenario")
    record_parser.add_argument("output", type=Path, help="output JSON scenario path")
    record_parser.add_argument(
        "--move-interval",
        type=float,
        default=0.01,
        help="minimum seconds between stored mouse move events (default: 0.01)",
    )
    record_parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.005,
        help="seconds between polling samples when --method poll is used (default: 0.005)",
    )
    record_parser.add_argument(
        "--method",
        choices=("poll", "hook", "raw"),
        default="poll",
        help="capture method. raw is best when games hide keyboard state from polling",
    )
    record_parser.add_argument(
        "--live",
        action="store_true",
        help="print each captured event to the console while recording",
    )

    play_parser = sub.add_parser("play", help="play a scenario")
    play_parser.add_argument("input", type=Path, help="input JSON scenario path")
    play_parser.add_argument("--speed", type=float, default=1.0, help="playback speed")
    play_parser.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="seconds to wait before replay starts (default: 3)",
    )

    inspect_parser = sub.add_parser("inspect", help="show scenario summary")
    inspect_parser.add_argument("input", type=Path, help="input JSON scenario path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            if args.move_interval <= 0:
                raise SystemExit("--move-interval must be greater than 0")
            if args.method == "hook":
                Recorder(args.output, args.move_interval, args.live).run()
            elif args.method == "raw":
                RawInputRecorder(args.output, args.move_interval, args.live).run()
            else:
                if args.poll_interval <= 0:
                    raise SystemExit("--poll-interval must be greater than 0")
                PollingRecorder(
                    args.output, args.move_interval, args.poll_interval, args.live
                ).run()
        elif args.command == "play":
            if args.speed <= 0:
                raise SystemExit("--speed must be greater than 0")
            play(args.input, args.speed, args.countdown)
        elif args.command == "inspect":
            inspect(args.input)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
