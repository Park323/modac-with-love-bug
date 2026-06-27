"""
Input replayer using ctypes SendInput + mouse_event directly.

No pyautogui layer:
- Keyboard: SendInput with VK codes (works in fullscreen)
- Mouse move: mouse_event(MOUSEEVENTF_MOVE, dx, dy) — relative, works in FPS raw-input mode
- Mouse buttons: mouse_event with button flags
"""

from __future__ import annotations

import ctypes
import json
import random
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .keys import NAME_TO_VK

_user32 = ctypes.windll.user32

# ── Windows input structures ──────────────────────────────────────────────────

INPUT_MOUSE    = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP       = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

MOUSEEVENTF_MOVE       = 0x0001
MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_RIGHTDOWN  = 0x0008
MOUSEEVENTF_RIGHTUP    = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP   = 0x0040


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wintypes.WORD),
        ("wScan",       wintypes.WORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          wintypes.LONG),
        ("dy",          wintypes.LONG),
        ("mouseData",   wintypes.DWORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT_UNION)]


def _send_key(vk: int, key_up: bool) -> None:
    flags = KEYEVENTF_KEYUP if key_up else 0
    inp = INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(wVk=vk, dwFlags=flags)))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_mouse_move(dx: int, dy: int) -> None:
    # mouse_event with MOUSEEVENTF_MOVE sends raw relative movement —
    # this is what FPS games pick up via raw input.
    _user32.mouse_event(MOUSEEVENTF_MOVE, ctypes.c_long(dx), ctypes.c_long(dy), 0, 0)


def _send_mouse_button(flag: int) -> None:
    _user32.mouse_event(flag, 0, 0, 0, 0)


_BUTTON_DOWN_FLAGS = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN, "middle": MOUSEEVENTF_MIDDLEDOWN}
_BUTTON_UP_FLAGS   = {"left": MOUSEEVENTF_LEFTUP,   "right": MOUSEEVENTF_RIGHTUP,   "middle": MOUSEEVENTF_MIDDLEUP}


# ── replayer ──────────────────────────────────────────────────────────────────

class InputReplayer:
    def __init__(self, jitter_ms: float = 2.0) -> None:
        """
        jitter_ms: ±ms of random timing noise added per event.
        Small jitter makes the replay timing pattern less machine-like.
        """
        self._jitter = jitter_ms / 1000.0
        self._running = False

    @property
    def is_replaying(self) -> bool:
        return self._running

    def replay(self, recording_path: str) -> None:
        path = Path(recording_path)
        if not path.exists():
            raise FileNotFoundError(f"Recording not found: {recording_path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        events: list[dict[str, Any]] = data.get("events", [])
        if not events:
            return

        self._running = True
        t_start = time.perf_counter()

        try:
            for event in events:
                if not self._running:
                    break
                target_t = float(event["t"])
                jitter = random.uniform(-self._jitter, self._jitter)
                wait = (t_start + target_t + jitter) - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                self._dispatch(event)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    def _dispatch(self, event: dict[str, Any]) -> None:
        t = event["type"]

        if t == "key_down":
            vk = NAME_TO_VK.get(event["key"])
            if vk:
                _send_key(vk, key_up=False)

        elif t == "key_up":
            vk = NAME_TO_VK.get(event["key"])
            if vk:
                _send_key(vk, key_up=True)

        elif t == "mouse_move":
            _send_mouse_move(event.get("dx", 0), event.get("dy", 0))

        elif t == "mouse_button_down":
            flag = _BUTTON_DOWN_FLAGS.get(event.get("button", "left"))
            if flag:
                _send_mouse_button(flag)

        elif t == "mouse_button_up":
            flag = _BUTTON_UP_FLAGS.get(event.get("button", "left"))
            if flag:
                _send_mouse_button(flag)
