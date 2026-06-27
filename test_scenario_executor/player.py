"""Play action JSON arrays received from an external app."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Iterable

from . import win_input as wi
from .keys import NAME_TO_VK, scan_code_for_vk
from .win_input import (
    MOUSEEVENTF_HWHEEL,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN,
    MOUSEEVENTF_MIDDLEUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_WHEEL,
    MOUSEEVENTF_XDOWN,
    MOUSEEVENTF_XUP,
)

_BUTTON_DOWN = {
    "left": MOUSEEVENTF_LEFTDOWN,
    "right": MOUSEEVENTF_RIGHTDOWN,
    "middle": MOUSEEVENTF_MIDDLEDOWN,
}
_BUTTON_UP = {
    "left": MOUSEEVENTF_LEFTUP,
    "right": MOUSEEVENTF_RIGHTUP,
    "middle": MOUSEEVENTF_MIDDLEUP,
}
_ACTION_FLAGS: dict[str, int] = {
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
}


class ActionPlayer:
    """Dispatch JSON actions as Windows keyboard and mouse input."""

    def __init__(self, jitter_ms: float = 0.0) -> None:
        self._jitter = jitter_ms / 1000.0
        self._running = False

    @property
    def is_playing(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._running = False

    def play_file(self, path: str | Path) -> int:
        with Path(path).open(encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            actions = payload.get("events") or payload.get("actions") or []
        else:
            actions = payload
        return self.play_actions(actions)

    def play_actions(self, actions: Iterable[dict[str, Any]]) -> int:
        action_list = list(actions)
        if not action_list:
            return 0

        self._running = True
        played = 0
        t_start = time.perf_counter()
        try:
            for action in action_list:
                if not self._running:
                    break
                self._wait_until(action, t_start)
                self._dispatch(action)
                played += 1
        finally:
            self._running = False
        return played

    def _wait_until(self, action: dict[str, Any], t_start: float) -> None:
        target_t = action.get("t", action.get("time", None))
        if target_t is None:
            delay_ms = action.get("delay_ms")
            if delay_ms:
                time.sleep(max(0.0, float(delay_ms) / 1000.0))
            return

        jitter = random.uniform(-self._jitter, self._jitter) if self._jitter else 0.0
        wait = (t_start + float(target_t) + jitter) - time.perf_counter()
        if wait > 0:
            time.sleep(wait)

    def _dispatch(self, action: dict[str, Any]) -> None:
        event_type = action.get("type")

        if event_type in ("key_down", "key_up"):
            self._send_key(
                key=action.get("key"),
                scan=action.get("scan"),
                extended=bool(action.get("extended", False)),
                is_up=event_type == "key_up",
            )
            return

        if event_type == "key_press":
            self._send_key(action.get("key"), action.get("scan"), bool(action.get("extended")), False)
            time.sleep(float(action.get("duration_ms", 30)) / 1000.0)
            self._send_key(action.get("key"), action.get("scan"), bool(action.get("extended")), True)
            return

        if event_type == "mouse_move":
            wi.send_mouse_relative(int(action.get("dx", 0)), int(action.get("dy", 0)))
            return

        if event_type == "mouse_move_abs":
            wi.send_mouse_absolute(
                int(action.get("x", 0)),
                int(action.get("y", 0)),
                MOUSEEVENTF_MOVE,
            )
            return

        if event_type == "mouse_button_down":
            flag = _BUTTON_DOWN.get(str(action.get("button", "left")))
            if flag:
                wi.send_mouse_button(flag)
            return

        if event_type == "mouse_button_up":
            flag = _BUTTON_UP.get(str(action.get("button", "left")))
            if flag:
                wi.send_mouse_button(flag)
            return

        if event_type == "mouse_click":
            button = str(action.get("button", "left"))
            down = _BUTTON_DOWN.get(button)
            up = _BUTTON_UP.get(button)
            if down and up:
                wi.send_mouse_button(down)
                time.sleep(float(action.get("duration_ms", 30)) / 1000.0)
                wi.send_mouse_button(up)
            return

        self._dispatch_legacy(action)

    def _dispatch_legacy(self, action: dict[str, Any]) -> None:
        kind = action.get("kind")
        if kind == "keyboard":
            self._send_key(
                key=action.get("key"),
                scan=action.get("scan"),
                extended=bool(action.get("extended", False)),
                is_up=action.get("action") == "up",
            )
            return

        if kind == "mouse":
            mouse_action = action.get("action")
            if mouse_action == "raw_move":
                wi.send_mouse_relative(int(action.get("dx", 0)), int(action.get("dy", 0)))
                return
            flags = _ACTION_FLAGS.get(str(mouse_action))
            if flags:
                data = int(action.get("delta", action.get("button", 0)))
                wi.send_mouse_absolute(
                    int(action.get("x", 0)),
                    int(action.get("y", 0)),
                    flags,
                    data,
                )

    def _send_key(
        self,
        key: Any,
        scan: Any,
        extended: bool,
        is_up: bool,
    ) -> None:
        scan_int = int(scan or 0)
        if scan_int:
            wi.send_keyboard_scan(scan_int, extended, is_up)
            return
        vk = NAME_TO_VK.get(str(key))
        if not vk:
            return
        fallback_scan = scan_code_for_vk(vk)
        if fallback_scan:
            wi.send_keyboard_scan(fallback_scan, extended, is_up)
        else:
            wi.send_keyboard_vk(vk, is_up)
