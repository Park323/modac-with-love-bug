from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


_USER32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

VK_CODES = {
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "CTRL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "MOUSE_LEFT": 0x01,
    "MOUSE_RIGHT": 0x02,
    "MOUSE_MIDDLE": 0x04,
}


def virtual_key_code(key_name: str) -> int:
    normalized = key_name.upper()
    if normalized in VK_CODES:
        return VK_CODES[normalized]
    if len(normalized) == 1:
        return ord(normalized)
    raise ValueError(f"Unsupported hotkey for Windows polling: {key_name}")


def is_pressed(key_name: str) -> bool:
    return bool(_USER32.GetAsyncKeyState(virtual_key_code(key_name)) & 0x8000)


def cursor_position() -> tuple[int, int]:
    point = POINT()
    _USER32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def wait_for_press(key_name: str, *, poll_interval_sec: float = 0.01) -> None:
    # Wait for a fresh press, not a key already held before arming.
    while is_pressed(key_name):
        time.sleep(poll_interval_sec)
    while not is_pressed(key_name):
        time.sleep(poll_interval_sec)
    while is_pressed(key_name):
        time.sleep(poll_interval_sec)
