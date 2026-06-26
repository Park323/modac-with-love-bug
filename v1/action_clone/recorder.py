from __future__ import annotations

import threading
import time
import winsound
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pynput import keyboard, mouse

from .io import write_json
from .keys import normalize_key
from .raw_input import RawInputRecorder
from .win_hotkeys import cursor_position, is_pressed, wait_for_press

STOP_KEY = "F9"
IGNORED_KEYS = {"F8"}
POLLING_KEYS = ["W", "A", "S", "D", "Ctrl", "Shift", "Space", "R", "G", "E", "Q", "B", "1", "2", "3", "4", "5"]
POLLING_MOUSE_BUTTONS = {
    "MOUSE_LEFT": "left",
    "MOUSE_RIGHT": "right",
    "MOUSE_MIDDLE": "middle",
}


def _beep(frequency: int, duration_ms: int, enabled: bool) -> None:
    if enabled:
        winsound.Beep(frequency, duration_ms)


def wait_for_start_hotkey(start_hotkey: str) -> None:
    normalized_hotkey = start_hotkey.upper()

    print(f"Armed. Focus the game and press {normalized_hotkey} to start recording.")
    try:
        wait_for_press(normalized_hotkey)
    except KeyboardInterrupt:
        raise


class InputRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        window_title: str,
        mouse_sample_hz: float,
    ) -> None:
        self.session_id = session_id
        self.window_title = window_title
        self.mouse_sample_interval = 1.0 / mouse_sample_hz if mouse_sample_hz > 0 else 0.0
        self.events: list[dict[str, Any]] = []
        self._pressed_keys: set[str] = set()
        self._last_mouse_time = 0.0
        self._last_mouse_pos: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t0 = 0.0

    def run(self, duration_sec: float | None) -> dict[str, Any]:
        self._t0 = time.perf_counter()
        stop_watcher = threading.Thread(target=self._watch_stop_key, daemon=True)
        key_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )

        stop_watcher.start()
        key_listener.start()
        mouse_listener.start()

        deadline = self._t0 + duration_sec if duration_sec else None
        try:
            while not self._stop.is_set():
                if deadline and time.perf_counter() >= deadline:
                    break
                time.sleep(0.02)
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Saving recording...")
        finally:
            key_listener.stop()
            mouse_listener.stop()
            key_listener.join(timeout=1.0)
            mouse_listener.join(timeout=1.0)

        duration = round(time.perf_counter() - self._t0, 6)
        with self._lock:
            events = sorted(self.events, key=lambda event: event["t"])

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
                "mouse_move_format": "relative_delta",
                "stop_key": STOP_KEY,
            },
            "events": events,
        }

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 6)

    def _append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(event)

    def _watch_stop_key(self) -> None:
        was_pressed = False
        while not self._stop.is_set():
            pressed = is_pressed(STOP_KEY)
            if pressed and not was_pressed:
                self._stop.set()
                return
            was_pressed = pressed
            time.sleep(0.01)

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode) -> bool | None:
        key_name = normalize_key(key)
        if key_name == STOP_KEY:
            self._stop.set()
            return False
        if key_name in IGNORED_KEYS or key_name in self._pressed_keys:
            return None

        self._pressed_keys.add(key_name)
        self._append({"t": self._elapsed(), "type": "key_down", "key": key_name})
        return None

    def _on_key_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        key_name = normalize_key(key)
        if key_name in IGNORED_KEYS or key_name == STOP_KEY:
            return
        self._pressed_keys.discard(key_name)
        self._append({"t": self._elapsed(), "type": "key_up", "key": key_name})

    def _on_mouse_move(self, x: int, y: int) -> None:
        now = time.perf_counter()
        if self._last_mouse_pos is None:
            self._last_mouse_pos = (x, y)
            self._last_mouse_time = now
            return
        if self.mouse_sample_interval and now - self._last_mouse_time < self.mouse_sample_interval:
            return

        last_x, last_y = self._last_mouse_pos
        dx = x - last_x
        dy = y - last_y
        self._last_mouse_pos = (x, y)
        self._last_mouse_time = now
        if dx == 0 and dy == 0:
            return
        self._append({"t": self._elapsed(), "type": "mouse_move", "dx": dx, "dy": dy})

    def _on_mouse_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        button_name = str(button).replace("Button.", "")
        event_type = "mouse_button_down" if pressed else "mouse_button_up"
        self._append(
            {
                "t": self._elapsed(),
                "type": event_type,
                "button": button_name,
                "x": x,
                "y": y,
            }
        )

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._append(
            {
                "t": self._elapsed(),
                "type": "mouse_scroll",
                "dx": dx,
                "dy": dy,
                "x": x,
                "y": y,
            }
        )


class PollingInputRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        window_title: str,
        mouse_sample_hz: float,
    ) -> None:
        self.session_id = session_id
        self.window_title = window_title
        self.sample_interval = 1.0 / mouse_sample_hz if mouse_sample_hz > 0 else 1 / 60
        self.events: list[dict[str, Any]] = []
        self._previous_keys: dict[str, bool] = {}
        self._previous_buttons: dict[str, bool] = {}
        self._last_mouse_pos: tuple[int, int] | None = None
        self._t0 = 0.0

    def run(self, duration_sec: float | None) -> dict[str, Any]:
        if duration_sec is None:
            raise ValueError("Polling backend requires --duration because fullscreen may block stop hotkeys.")

        self._t0 = time.perf_counter()
        deadline = self._t0 + duration_sec
        self._last_mouse_pos = cursor_position()
        try:
            while time.perf_counter() < deadline:
                self._sample_keys()
                self._sample_mouse_buttons()
                self._sample_mouse_move()
                time.sleep(self.sample_interval)
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Saving recording...")
        finally:
            self._release_any_pressed_inputs()

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
                "backend": "polling",
                "mouse_move_format": "relative_delta_from_cursor_position",
                "note": "Polling samples key/button state and may miss raw FPS mouse deltas.",
            },
            "events": sorted(self.events, key=lambda event: event["t"]),
        }

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 6)

    def _sample_keys(self) -> None:
        for key_name in POLLING_KEYS:
            pressed = is_pressed(key_name)
            was_pressed = self._previous_keys.get(key_name, False)
            if pressed and not was_pressed:
                self.events.append({"t": self._elapsed(), "type": "key_down", "key": key_name})
            elif was_pressed and not pressed:
                self.events.append({"t": self._elapsed(), "type": "key_up", "key": key_name})
            self._previous_keys[key_name] = pressed

    def _sample_mouse_buttons(self) -> None:
        for vk_name, button_name in POLLING_MOUSE_BUTTONS.items():
            pressed = is_pressed(vk_name)
            was_pressed = self._previous_buttons.get(vk_name, False)
            if pressed and not was_pressed:
                self.events.append({"t": self._elapsed(), "type": "mouse_button_down", "button": button_name})
            elif was_pressed and not pressed:
                self.events.append({"t": self._elapsed(), "type": "mouse_button_up", "button": button_name})
            self._previous_buttons[vk_name] = pressed

    def _sample_mouse_move(self) -> None:
        current = cursor_position()
        if self._last_mouse_pos is None:
            self._last_mouse_pos = current
            return
        last_x, last_y = self._last_mouse_pos
        dx = current[0] - last_x
        dy = current[1] - last_y
        self._last_mouse_pos = current
        if dx or dy:
            self.events.append({"t": self._elapsed(), "type": "mouse_move", "dx": dx, "dy": dy})

    def _release_any_pressed_inputs(self) -> None:
        for key_name, pressed in self._previous_keys.items():
            if pressed:
                self.events.append({"t": self._elapsed(), "type": "key_up", "key": key_name})
        for vk_name, pressed in self._previous_buttons.items():
            if pressed:
                self.events.append(
                    {
                        "t": self._elapsed(),
                        "type": "mouse_button_up",
                        "button": POLLING_MOUSE_BUTTONS[vk_name],
                    }
                )


def record_session(
    *,
    output_path: Path,
    session_id: str,
    duration_sec: float | None,
    countdown_sec: float,
    start_hotkey: str | None,
    window_title: str,
    mouse_sample_hz: float,
    beep: bool,
    backend: str,
) -> None:
    print("Input recording will start soon.")
    print(f"Output: {output_path}")
    if start_hotkey:
        print(f"Start key: {start_hotkey.upper()}")
    print(f"Stop key: {STOP_KEY}")
    if duration_sec:
        print(f"Duration limit: {duration_sec:.1f}s")

    if start_hotkey:
        wait_for_start_hotkey(start_hotkey)
    elif countdown_sec > 0:
        print(f"Starting in {countdown_sec:.1f}s...")
        remaining = int(countdown_sec)
        for second in range(remaining, 0, -1):
            if second <= 3:
                _beep(880, 120, beep)
            print(f"{second}...")
            time.sleep(1)
        fractional = countdown_sec - remaining
        if fractional > 0:
            time.sleep(fractional)

    _beep(1320, 250, beep)
    print(f"Recording now with {backend} backend. Press F9 to stop if supported.")
    recorder_cls = {
        "hook": InputRecorder,
        "polling": PollingInputRecorder,
        "raw": RawInputRecorder,
    }[backend]
    recorder = recorder_cls(
        session_id=session_id,
        window_title=window_title,
        mouse_sample_hz=mouse_sample_hz,
    )
    payload = recorder.run(duration_sec=duration_sec)
    write_json(output_path, payload)
    _beep(660, 400, beep)
    print(f"Saved {len(payload['events'])} events to {output_path}")
