from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pyautogui
import pygetwindow as gw

from .io import read_json
from .keys import to_pyautogui_key


def _focus_window(window_title: str) -> None:
    windows = gw.getWindowsWithTitle(window_title)
    if not windows:
        raise RuntimeError(f"No window found with title containing: {window_title}")

    window = windows[0]
    if window.isMinimized:
        window.restore()
    window.activate()


def _sleep_until_event(start: float, event_t: float, speed: float) -> None:
    target = start + (event_t / speed)
    remaining = target - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)


def _send_event(event: dict[str, Any]) -> None:
    event_type = event["type"]
    if event_type == "key_down":
        pyautogui.keyDown(to_pyautogui_key(event["key"]))
        return
    if event_type == "key_up":
        pyautogui.keyUp(to_pyautogui_key(event["key"]))
        return
    if event_type == "mouse_move":
        pyautogui.moveRel(event.get("dx", 0), event.get("dy", 0), duration=0)
        return
    if event_type == "mouse_button_down":
        pyautogui.mouseDown(button=event.get("button", "left"))
        return
    if event_type == "mouse_button_up":
        pyautogui.mouseUp(button=event.get("button", "left"))
        return
    if event_type == "mouse_scroll":
        pyautogui.scroll(event.get("dy", 0))
        return

    raise ValueError(f"Unsupported event type: {event_type}")


def _release_pressed_keys(events: list[dict[str, Any]]) -> None:
    keys = {event["key"] for event in events if event["type"] == "key_down"}
    for key in keys:
        try:
            pyautogui.keyUp(to_pyautogui_key(key))
        except Exception:
            pass


def replay_session(
    *,
    recording_path: Path,
    window_title: str | None,
    start_delay_sec: float,
    speed: float,
    repeat: int,
    dry_run: bool,
    focus_window: bool,
) -> None:
    if speed <= 0:
        raise ValueError("--speed must be greater than 0.")
    if repeat <= 0:
        raise ValueError("--repeat must be greater than 0.")

    payload = read_json(recording_path)
    events = payload.get("events", [])
    if not events:
        raise RuntimeError(f"No events found in recording: {recording_path}")

    session = payload.get("session", {})
    target_title = window_title or session.get("window_title") or "CrossFire"

    print(f"Loaded {len(events)} events from {recording_path}")
    print(f"Target window: {target_title}")
    print(f"Repeat: {repeat}, speed: {speed}x, dry_run: {dry_run}")

    if dry_run:
        for index, event in enumerate(events[:20], start=1):
            print(f"{index:03d}: t={event.get('t')} {event}")
        if len(events) > 20:
            print(f"... {len(events) - 20} more events")
        return

    pyautogui.FAILSAFE = True
    if focus_window:
        _focus_window(target_title)

    if start_delay_sec > 0:
        print(f"Replay starts in {start_delay_sec:.1f}s. Move mouse to a screen corner to abort.")
        time.sleep(start_delay_sec)

    try:
        for run_index in range(repeat):
            print(f"Replay run {run_index + 1}/{repeat}")
            start = time.perf_counter()
            for event in events:
                _sleep_until_event(start, float(event.get("t", 0.0)), speed)
                _send_event(event)
            time.sleep(0.2)
    except pyautogui.FailSafeException:
        print("PyAutoGUI fail-safe triggered. Replay aborted.")
    finally:
        _release_pressed_keys(events)
