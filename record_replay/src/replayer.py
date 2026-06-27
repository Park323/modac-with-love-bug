"""
Input replayer — reads recorded events and dispatches via win_input.py.

Keyboard: scan-code path if 'scan' field present (v0.2+ recordings),
          VK-code fallback for older recordings without 'scan'.
Mouse:    relative move for 'mouse_move' events (FPS raw-input compatible),
          absolute move for 'move' events (hook/poll screen coords).
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from . import win_input as wi
from .win_input import (
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP,
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_MOVE,
)
from .keys import NAME_TO_VK

_BUTTON_DOWN = {
    "left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
    "middle": MOUSEEVENTF_MIDDLEDOWN,
}
_BUTTON_UP = {
    "left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
    "middle": MOUSEEVENTF_MIDDLEUP,
}

# teammate's event action names → mouse flags (for hook/poll recordings)
_ACTION_FLAGS: dict[str, int] = {
    "move":        MOUSEEVENTF_MOVE,
    "left_down":   MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN,
    "left_up":     MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP,
    "right_down":  MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTDOWN,
    "right_up":    MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTUP,
    "middle_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_MIDDLEDOWN,
    "middle_up":   MOUSEEVENTF_MOVE | MOUSEEVENTF_MIDDLEUP,
    "wheel":       MOUSEEVENTF_MOVE | MOUSEEVENTF_WHEEL,
    "hwheel":      MOUSEEVENTF_MOVE | MOUSEEVENTF_HWHEEL,
    "x_down":      MOUSEEVENTF_MOVE | MOUSEEVENTF_XDOWN,
    "x_up":        MOUSEEVENTF_MOVE | MOUSEEVENTF_XUP,
}


class InputReplayer:
    def __init__(self, jitter_ms: float = 2.0) -> None:
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

    # ── dispatch ─────────────────────────────────────────────────────────────

    def _dispatch(self, event: dict[str, Any]) -> None:
        # ── v0.2 format (our recorder) ──
        t = event.get("type")

        if t in ("key_down", "key_up"):
            is_up = t == "key_up"
            if "scan" in event and event["scan"]:
                # scan-code path (teammate's injection, better game compat)
                wi.send_keyboard_scan(event["scan"], event.get("extended", False), is_up)
            else:
                # VK fallback for old recordings
                vk = NAME_TO_VK.get(event.get("key", ""))
                if vk:
                    wi.send_keyboard_vk(vk, is_up)
            return

        if t == "mouse_move":
            wi.send_mouse_relative(event.get("dx", 0), event.get("dy", 0))
            return

        if t == "mouse_button_down":
            flag = _BUTTON_DOWN.get(event.get("button", "left"))
            if flag:
                wi.send_mouse_button(flag)
            return

        if t == "mouse_button_up":
            flag = _BUTTON_UP.get(event.get("button", "left"))
            if flag:
                wi.send_mouse_button(flag)
            return

        # ── teammate's format (kind/action) ──
        kind = event.get("kind")

        if kind == "keyboard":
            is_up = event.get("action") == "up"
            scan = event.get("scan", 0)
            if scan:
                wi.send_keyboard_scan(scan, event.get("extended", False), is_up)
            else:
                vk = NAME_TO_VK.get(event.get("key", ""))
                if vk:
                    wi.send_keyboard_vk(vk, is_up)
            return

        if kind == "mouse":
            action = event.get("action")
            if action == "raw_move":
                wi.send_mouse_relative(event.get("dx", 0), event.get("dy", 0))
                return
            flags = _ACTION_FLAGS.get(action)
            if flags:
                data = int(event.get("delta", event.get("button", 0)))
                wi.send_mouse_absolute(event.get("x", 0), event.get("y", 0), flags, data)
