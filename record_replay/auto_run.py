"""
Auto-run entry point:
  1. Load accomplish snippet from file
  2. Wait for F8 (start as GR) or F9 (start as BL)
  3. AutoNavigator drives the character while recording
  4. Save recording when all waypoints done or F10 is pressed

Usage:
  python record_replay/auto_run.py assets/accomplish_snippet.json

Hotkeys (while waiting OR while running):
  F8  → start auto-run from GR spawn  (x=1901, y=123,  rot=270)
  F9  → start auto-run from BL spawn  (x=116,  y=261,  rot=90)
  F10 → stop and save immediately

Run as Administrator (required to send input to CrossFire).
"""

from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from record_replay.src.navigator import AutoNavigator, Waypoint

# ── spawn positions ───────────────────────────────────────────────────────────

BL_SPAWN = {"x": 116.0,  "y": 261.0, "rot": 90.0}
GR_SPAWN = {"x": 1901.0, "y": 123.0, "rot": 270.0}

VK_F8  = 0x77
VK_F9  = 0x78
VK_F10 = 0x79

_user32 = ctypes.windll.user32


def _is_pressed(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _wait_release(vk: int) -> None:
    while _is_pressed(vk):
        time.sleep(0.01)


def _beep_countdown() -> None:
    for i in range(3, 0, -1):
        print(f"[AUTO] Starting in {i}...", flush=True)
        winsound.Beep(880, 120)
        time.sleep(1)


def load_snippet(path: str) -> list[Waypoint]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("waypoints", data) if isinstance(data, dict) else data
    return [Waypoint.from_dict(w) for w in raw if w.get("position")]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python record_replay/auto_run.py <snippet_file>")
        print("  e.g. python record_replay/auto_run.py assets/accomplish_snippet.json")
        sys.exit(1)

    snippet_path = sys.argv[1]
    if not Path(snippet_path).exists():
        print(f"[ERROR] Snippet file not found: {snippet_path}")
        sys.exit(1)

    waypoints = load_snippet(snippet_path)
    print(f"[AUTO] Loaded {len(waypoints)} waypoint(s) from {snippet_path}")

    print()
    print("=" * 50)
    print(f"  Snippet : {snippet_path}")
    print(f"  F8  → start as GR  (x=1901, y=123,  rot=270°)")
    print(f"  F9  → start as BL  (x=116,  y=261,  rot=90°)")
    print(f"  F10 → stop and save")
    print("=" * 50)

    # ── wait for team selection ───────────────────────────────────────────────
    team        = None
    start_state = None
    prev_f8 = prev_f9 = prev_f10 = False

    while team is None:
        f8  = _is_pressed(VK_F8)
        f9  = _is_pressed(VK_F9)
        f10 = _is_pressed(VK_F10)

        if f8 and not prev_f8:
            _wait_release(VK_F8)
            team        = "GR"
            start_state = GR_SPAWN

        if f9 and not prev_f9:
            _wait_release(VK_F9)
            team        = "BL"
            start_state = BL_SPAWN

        if f10 and not prev_f10:
            print("[AUTO] Quit")
            sys.exit(0)

        prev_f8, prev_f9, prev_f10 = f8, f9, f10
        time.sleep(0.01)

    print(f"[AUTO] Team: {team} — starting at "
          f"({start_state['x']:.0f}, {start_state['y']:.0f})  rot={start_state['rot']:.0f}°")
    _beep_countdown()

    session_id  = f"auto_{team}_{int(time.time())}"
    output_path = f"record_replay/recordings/{session_id}.json"
    navigator   = AutoNavigator()

    # ── run navigator in background thread ────────────────────────────────────
    nav_thread = threading.Thread(
        target=navigator.run,
        kwargs=dict(
            waypoints=waypoints,
            output_path=output_path,
            session_id=session_id,
            start_state=start_state,
        ),
        daemon=True,
    )
    nav_thread.start()
    print(f"[AUTO] Running — press F10 to stop and save at any time")

    # ── monitor F10 for emergency stop ────────────────────────────────────────
    prev_f10 = False
    while nav_thread.is_alive():
        f10 = _is_pressed(VK_F10)
        if f10 and not prev_f10:
            _wait_release(VK_F10)
            print("[AUTO] F10 pressed — stopping and saving...")
            navigator.stop()
        prev_f10 = f10
        time.sleep(0.01)

    nav_thread.join()
    print("[AUTO] Done.")


if __name__ == "__main__":
    main()
