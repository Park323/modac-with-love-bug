"""
Hook-free input recorder — polling via GetAsyncKeyState + GetCursorPos.

No pynput, no WH_MOUSE_LL, no WH_KEYBOARD_LL.
Tradeoff: mouse delta from GetCursorPos returns (0,0) in FPS raw-input mode.
Keyboard and mouse button capture is reliable.

Each key event stores 'scan' and 'extended' fields so the replayer
can use the scan-code path (see win_input.py).
"""

from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .keys import (
    ALL_KEYBOARD_VKS,
    EXTENDED_VKS,
    MOUSE_VK_TO_NAME,
    VK_TO_NAME,
    scan_code_for_vk,
)

_user32 = ctypes.windll.user32

SAMPLE_HZ = 120


def _is_pressed(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class PollingRecorder:
    def __init__(self, sample_hz: float = SAMPLE_HZ) -> None:
        self._interval = 1.0 / sample_hz
        self.events: list[dict[str, Any]] = []
        self._t0 = 0.0
        self._prev_keys: dict[int, bool] = {}
        self._prev_buttons: dict[int, bool] = {}
        self._prev_cursor: tuple[int, int] | None = None
        self._running = False

    # ── public ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Run polling loop (blocks until stop() is called)."""
        self.events = []
        self._prev_keys = {}
        self._prev_buttons = {}
        self._prev_cursor = _cursor_pos()
        self._t0 = time.perf_counter()
        self._running = True

        while self._running:
            self._poll_keys()
            self._poll_mouse_buttons()
            self._poll_cursor()
            time.sleep(self._interval)

    def stop(self) -> None:
        self._running = False

    @property
    def is_recording(self) -> bool:
        return self._running

    def save(self, path: str, session_id: str = "session") -> dict:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        duration = self.events[-1]["t"] if self.events else 0.0
        data = {
            "schema_version": "0.2",
            "session": {
                "session_id": session_id,
                "game": "CrossFire",
                "mode": "Team Deathmatch",
                "map": "Transport Ship 2.0",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "duration_sec": round(duration, 4),
                "event_count": len(self.events),
            },
            "environment": {
                "backend": "polling",
                "note": "mouse_move dx/dy from GetCursorPos — zero in FPS raw-input mode",
            },
            "events": self.events,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    # ── polling ──────────────────────────────────────────────────────────────

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 4)

    def _poll_keys(self) -> None:
        t = self._elapsed()
        for vk in ALL_KEYBOARD_VKS:
            pressed = _is_pressed(vk)
            was = self._prev_keys.get(vk, False)
            if pressed == was:
                continue

            self._prev_keys[vk] = pressed
            scan = scan_code_for_vk(vk)
            if scan == 0:
                continue

            self.events.append({
                "t":        t,
                "type":     "key_down" if pressed else "key_up",
                "key":      VK_TO_NAME.get(vk, f"0x{vk:02X}"),
                "scan":     scan,
                "extended": vk in EXTENDED_VKS,
            })

    def _poll_mouse_buttons(self) -> None:
        t = self._elapsed()
        for vk, name in MOUSE_VK_TO_NAME.items():
            pressed = _is_pressed(vk)
            was = self._prev_buttons.get(vk, False)
            if pressed == was:
                continue
            self._prev_buttons[vk] = pressed
            self.events.append({
                "t":      t,
                "type":   "mouse_button_down" if pressed else "mouse_button_up",
                "button": name,
            })

    def _poll_cursor(self) -> None:
        cur = _cursor_pos()
        if self._prev_cursor is not None:
            dx = cur[0] - self._prev_cursor[0]
            dy = cur[1] - self._prev_cursor[1]
            if dx or dy:
                self.events.append({
                    "t": self._elapsed(), "type": "mouse_move",
                    "dx": dx, "dy": dy,
                })
        self._prev_cursor = cur
