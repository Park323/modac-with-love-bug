"""
Standalone hotkey runner — no pynput, no global hooks.
Polls F8/F9/F10 via GetAsyncKeyState in a tight loop.

F8  → start recording
F9  → stop recording + save
F10 → quit
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from record_replay.src.recorder import PollingRecorder
from record_replay.src.detector import ScreenDetector

SESSION_ID      = f"tdm_run_{int(time.time())}"
OUTPUT_PATH     = f"record_replay/recordings/{SESSION_ID}.json"
TEMPLATES_DIR   = "record_replay/templates"

VK_F7  = 0x76
VK_F8  = 0x77
VK_F9  = 0x78
VK_F10 = 0x79

_user32 = ctypes.windll.user32
_detector = ScreenDetector(templates_dir=TEMPLATES_DIR)


def _is_pressed(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _wait_release(vk: int) -> None:
    while _is_pressed(vk):
        time.sleep(0.01)


recorder = PollingRecorder(sample_hz=120)
_record_thread: threading.Thread | None = None
_capture_counter = 0


def _capture_screen() -> None:
    global _capture_counter
    _capture_counter += 1
    name = f"capture_{_capture_counter:03d}"
    path = _detector.save_screenshot(name)
    print(f"[HOTKEY] Screen captured → {path}")


def _start_recording() -> None:
    global _record_thread
    if recorder.is_recording:
        print("[HOTKEY] Already recording")
        return
    for i in range(5, 0, -1):
        print(f"[HOTKEY] Starting in {i}...", flush=True)
        if i <= 3:
            winsound.Beep(880, 120)
        time.sleep(1)
    _record_thread = threading.Thread(target=recorder.start, daemon=True)
    _record_thread.start()
    print("[HOTKEY] Recording STARTED  (F9 to stop)")


def _stop_recording() -> None:
    if not recorder.is_recording:
        print("[HOTKEY] Not recording")
        return
    recorder.stop()
    if _record_thread:
        _record_thread.join(timeout=1.0)
    result = recorder.save(OUTPUT_PATH, SESSION_ID)
    print(f"[HOTKEY] Recording STOPPED  → {OUTPUT_PATH}")
    print(f"         events: {result['session']['event_count']}, "
          f"duration: {result['session']['duration_sec']}s")


print("=" * 44)
print(f"  Session : {SESSION_ID}")
print(f"  Output  : {OUTPUT_PATH}")
print("  F7  → capture screen")
print("  F8  → start recording")
print("  F9  → stop  recording")
print("  F10 → quit")
print("=" * 44)

prev_f7 = prev_f8 = prev_f9 = prev_f10 = False

while True:
    f7  = _is_pressed(VK_F7)
    f8  = _is_pressed(VK_F8)
    f9  = _is_pressed(VK_F9)
    f10 = _is_pressed(VK_F10)

    if f7 and not prev_f7:
        _wait_release(VK_F7)
        _capture_screen()

    if f8 and not prev_f8:
        _wait_release(VK_F8)
        _start_recording()

    if f9 and not prev_f9:
        _wait_release(VK_F9)
        _stop_recording()

    if f10 and not prev_f10:
        print("[HOTKEY] Quitting")
        if recorder.is_recording:
            _stop_recording()
        break

    prev_f7, prev_f8, prev_f9, prev_f10 = f7, f8, f9, f10
    time.sleep(0.01)
