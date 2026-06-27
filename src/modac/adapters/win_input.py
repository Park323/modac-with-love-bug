"""Low-level Windows input injection via SendInput.

Uses hardware *scan codes* for keys (more reliable with DirectInput games than
virtual-key codes) and *relative* mouse motion for FPS mouselook.

Windows-only: importing this on another OS raises at call time, not import
time, so the package still imports for development elsewhere.
"""

from __future__ import annotations

import ctypes
import sys

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _ULONG_PTR = wintypes.WPARAM

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1

    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    def _send(*inputs: "_INPUT") -> None:
        n = len(inputs)
        arr = (_INPUT * n)(*inputs)
        _user32.SendInput(n, arr, ctypes.sizeof(_INPUT))

else:  # pragma: no cover - non-Windows
    def _send(*inputs):
        raise RuntimeError("win_input is only usable on Windows.")


# Scan codes (set 1) for common FPS keys.
SCAN = {
    "w": 0x11, "a": 0x1E, "s": 0x1F, "d": 0x20,
    "space": 0x39, "ctrl": 0x1D, "shift": 0x2A, "r": 0x13, "e": 0x12,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A,
}


def _key(scan: int, up: bool) -> "_INPUT":
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    ki = _KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return _INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))


def key_down(name: str) -> None:
    _send(_key(SCAN[name], up=False))


def key_up(name: str) -> None:
    _send(_key(SCAN[name], up=True))


def tap(name: str) -> None:
    _send(_key(SCAN[name], up=False), _key(SCAN[name], up=True))


def mouse_move(dx: int, dy: int) -> None:
    """Relative mouse motion (raw counts)."""
    mi = _MOUSEINPUT(dx=int(dx), dy=int(dy), mouseData=0,
                     dwFlags=MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0)
    _send(_INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=mi)))


def mouse_button(button: str, down: bool) -> None:
    if button == "left":
        flag = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    elif button == "right":
        flag = MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP
    else:
        raise ValueError(f"unknown button {button!r}")
    mi = _MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)
    _send(_INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=mi)))
