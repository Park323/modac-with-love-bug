"""
Low-level Windows input injection.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TEAMMATE SECTION — this is the file to edit when
  updating injection logic, adding new event types,
  or changing how input is sent to the game.

  Public interface (used by replayer.py):
    send_keyboard_scan(scan, extended, is_up)  ← primary path
    send_keyboard_vk(vk, is_up)                ← fallback for old recordings
    send_mouse_relative(dx, dy)                 ← FPS raw-input move
    send_mouse_absolute(x, y, flags, data)      ← hook/poll recorded positions
    send_mouse_button(flag)                     ← button down/up
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32

# ── constants ─────────────────────────────────────────────────────────────────

INPUT_MOUSE    = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP       = 0x0002
KEYEVENTF_SCANCODE    = 0x0008

MOUSEEVENTF_MOVE        = 0x0001
MOUSEEVENTF_LEFTDOWN    = 0x0002
MOUSEEVENTF_LEFTUP      = 0x0004
MOUSEEVENTF_RIGHTDOWN   = 0x0008
MOUSEEVENTF_RIGHTUP     = 0x0010
MOUSEEVENTF_MIDDLEDOWN  = 0x0020
MOUSEEVENTF_MIDDLEUP    = 0x0040
MOUSEEVENTF_XDOWN       = 0x0080
MOUSEEVENTF_XUP         = 0x0100
MOUSEEVENTF_WHEEL       = 0x0800
MOUSEEVENTF_HWHEEL      = 0x1000
MOUSEEVENTF_ABSOLUTE    = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN  = 76
SM_YVIRTUALSCREEN  = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# ── ctypes structures ─────────────────────────────────────────────────────────

ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wintypes.WORD),
        ("wScan",       wintypes.WORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          wintypes.LONG),
        ("dy",          wintypes.LONG),
        ("mouseData",   wintypes.DWORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


# ── keyboard ─────────────────────────────────────────────────────────────────

def send_keyboard_scan(scan: int, extended: bool, is_up: bool) -> None:
    """Primary keyboard path: scan-code based (hardware level, game-compatible)."""
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if is_up:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags)),
    )
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_keyboard_vk(vk: int, is_up: bool) -> None:
    """Fallback keyboard path: VK-code based (used for old recordings without scan field)."""
    flags = KEYEVENTF_KEYUP if is_up else 0
    inp = INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags)),
    )
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ── mouse ─────────────────────────────────────────────────────────────────────

def send_mouse_relative(dx: int, dy: int) -> None:
    """Relative mouse movement — works with FPS raw-input mode."""
    inp = INPUT(
        type=INPUT_MOUSE,
        union=_INPUT_UNION(mi=MOUSEINPUT(dx=dx, dy=dy, dwFlags=MOUSEEVENTF_MOVE)),
    )
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_mouse_absolute(x: int, y: int, flags: int, data: int = 0) -> None:
    """Absolute mouse movement — used when hook/poll recordings store screen coords."""
    left   = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top    = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width  = max(1, _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
    height = max(1, _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
    nx = int((x - left) * 65535 / width)
    ny = int((y - top) * 65535 / height)
    inp = INPUT(
        type=INPUT_MOUSE,
        union=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=nx, dy=ny, mouseData=data,
                dwFlags=flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            )
        ),
    )
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_mouse_button(flag: int, data: int = 0) -> None:
    """Send a mouse button event (down or up) without moving the cursor."""
    inp = INPUT(
        type=INPUT_MOUSE,
        union=_INPUT_UNION(mi=MOUSEINPUT(mouseData=data, dwFlags=flag)),
    )
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
