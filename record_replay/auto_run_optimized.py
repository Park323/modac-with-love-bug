"""
Auto-run (optimized): same as auto_run.py but handles in-game variables that
cause path deviation:

  1. Enemy shooting   → stutter / push  → re-route from current position
  2. Killed & Respawn → position resets  → detect & restart snippet from top
  3. Game bug / lag   → stutter / push  → re-route from current position

Key additions over auto_run.py:
  - Respawn position recorded at start
  - Every poll: compare current pos to spawn → if near spawn after moving away,
    treat as "killed" and restart the whole snippet (up to MAX_RESTARTS times)
  - On walk timeout (cases 1/3): re-compute A* from current position instead
    of giving up

Usage:
  python record_replay/auto_run_optimized.py assets/accomplish_snippet.json

Hotkeys:
  F8  → start as GR  (x=1901, y=123,  rot=270)
  F9  → start as BL  (x=116,  y=261,  rot=90)
  F10 → stop and save immediately
"""

from __future__ import annotations

import ctypes
import json
import math
import sys
import threading
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from record_replay.src.navigator import (
    AutoNavigator,
    Waypoint,
    REACH_THRESHOLD_PX,
    FINAL_REACH_PX,
    NAV_POLL_HZ,
    WAYPOINT_TIMEOUT_SEC,
)

# ── tuning ────────────────────────────────────────────────────────────────────

RESPAWN_RADIUS_PX      = 80.0   # within this distance of spawn = "just respawned"
MOVED_THRESHOLD_PX     = 150.0  # must travel this far from spawn before respawn can trigger
TELEPORT_THRESHOLD_PX  = 200.0  # position jump larger than this in one poll = teleport
MAX_RESTARTS           = 5      # abort after this many respawn recoveries
MAX_REROUTES           = 5      # max A* recomputes per single waypoint

RESPAWN_SETTLE_MAX_SEC  = 8.0   # max time to wait for respawn animation to finish
RESPAWN_SETTLE_DIST_PX  = 5.0   # movement below this = "not moving" (settled)
RESPAWN_SETTLE_POLLS    = 5     # consecutive settled polls needed to confirm ready

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


# ── optimized navigator ───────────────────────────────────────────────────────

class OptimizedNavigator(AutoNavigator):
    """
    Extends AutoNavigator with:
      - Respawn detection  → restart snippet from beginning
      - Path re-route      → recompute A* from current pos after timeout/push
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._spawn_position:      dict | None = None
        self._max_dist_from_spawn: float       = 0.0

    # ── public ────────────────────────────────────────────────────────────────

    def run(
        self,
        waypoints:   list[Waypoint],
        output_path: str,
        session_id:  str = "auto_run_opt",
        start_state: dict | None = None,
    ) -> dict:
        self._spawn_position      = dict(start_state) if start_state else None
        self._max_dist_from_spawn = 0.0
        self._start_state         = start_state
        self._running             = True
        self._start_recording()
        try:
            self._run_loop(waypoints)
            time.sleep(0.3)
        finally:
            self._running = False
            result = self._stop_recording(output_path, session_id)
            print(f"[NAV] Recording saved → {output_path}"
                  f"  ({result['session']['event_count']} events,"
                  f"  {result['session']['duration_sec']}s)")
        return result

    # ── main loop with respawn recovery ───────────────────────────────────────

    def _run_loop(self, waypoints: list[Waypoint]) -> None:
        wp_index      = 0
        restart_count = 0

        while wp_index < len(waypoints) and self._running:
            wp = waypoints[wp_index]
            print(f"[NAV] Waypoint {wp_index + 1}/{len(waypoints)}: "
                  f"({wp.x:.0f}, {wp.y:.0f})  rot={wp.rot:.0f}°")

            signal = self._navigate_to_optimized(wp)

            if signal == "respawned":
                restart_count += 1
                if restart_count > MAX_RESTARTS:
                    print(f"[NAV] Respawn limit reached ({MAX_RESTARTS}) — aborting")
                    self._running = False
                    return

                print(f"[NAV] Respawn #{restart_count} — waiting for respawn to settle")
                self._max_dist_from_spawn = 0.0
                self._wait_for_respawn_settle()
                wp_index = 0                       # restart from beginning
                continue

            wp_index += 1

        if self._running:
            print("[NAV] Mission complete — all waypoints reached")
        else:
            print("[NAV] Stopped early")

    # ── navigate to one waypoint (with re-route on deviation) ─────────────────

    def _navigate_to_optimized(self, target: Waypoint) -> str:
        """
        Navigate to target, recomputing A* path from current position whenever
        a timeout occurs (handles push/stutter from cases 1 and 3).

        Returns: 'ok' | 'respawned'
        """
        for attempt in range(MAX_REROUTES):
            if not self._running:
                return "ok"

            state = self._get_current_state()
            if state is None:
                print("[NAV]   No position data — skipping")
                return "ok"

            # (Re-)compute A* path from current position
            if self._pathfinder:
                path_pts = self._pathfinder.find_path(
                    (state["x"], state["y"]),
                    (target.x,   target.y),
                )
                if attempt > 0:
                    print(f"[NAV]   Re-route #{attempt}: "
                          f"{len(path_pts)} point(s) from "
                          f"({state['x']:.0f}, {state['y']:.0f})")
            else:
                path_pts = [(target.x, target.y)]

            # Walk each intermediate point
            needs_reroute = False
            for ix, (px, py) in enumerate(path_pts):
                if not self._running:
                    return "ok"

                is_final  = (ix == len(path_pts) - 1)
                threshold = FINAL_REACH_PX if is_final else REACH_THRESHOLD_PX

                signal = self._walk_to_optimized(px, py, threshold)

                if signal == "respawned":
                    return "respawned"

                if signal == "timeout":
                    # pushed off path — break to recompute A* from current pos
                    needs_reroute = True
                    break

            if not needs_reroute:
                # reached final point — apply rotation
                state = self._get_current_state()
                if state and self._running:
                    self._rotate_to(target.rot, state["rot"])
                return "ok"

        print(f"[NAV]   Could not reach waypoint after {MAX_REROUTES} re-routes — moving on")
        return "ok"

    # ── respawn settle wait ───────────────────────────────────────────────────

    def _wait_for_respawn_settle(self) -> None:
        """
        Wait until the character stops moving near spawn (respawn animation done).
        Polls position every 0.1s. Exits when position hasn't moved more than
        RESPAWN_SETTLE_DIST_PX for RESPAWN_SETTLE_POLLS consecutive polls,
        or when RESPAWN_SETTLE_MAX_SEC is exceeded.
        """
        deadline     = time.perf_counter() + RESPAWN_SETTLE_MAX_SEC
        stable_count = 0
        prev         = self._get_current_state()

        while time.perf_counter() < deadline and self._running:
            time.sleep(0.1)
            curr = self._get_current_state()
            if curr is None or prev is None:
                break

            moved = math.hypot(curr["x"] - prev["x"], curr["y"] - prev["y"])
            if moved < RESPAWN_SETTLE_DIST_PX:
                stable_count += 1
                if stable_count >= RESPAWN_SETTLE_POLLS:
                    print(f"[NAV]   Respawn settled at "
                          f"({curr['x']:.0f}, {curr['y']:.0f})")
                    return
            else:
                stable_count = 0

            prev = curr

        print("[NAV]   Respawn settle timeout — proceeding")

    # ── walk with respawn detection ───────────────────────────────────────────

    def _walk_to_optimized(self, tx: float, ty: float, threshold: float) -> str:
        """
        Walk toward (tx, ty). Each poll cycle:
          1. Check if position teleported to spawn  → return 'respawned'
          2. Check if reached target               → return 'ok'
          3. Otherwise steer and step forward

        Respawn is detected only when BOTH conditions are true:
          - position jumped > TELEPORT_THRESHOLD_PX in a single poll
          - new position is within RESPAWN_RADIUS_PX of spawn
        This avoids false positives when the route legitimately passes near spawn.

        Returns: 'ok' | 'timeout' | 'respawned'
        """
        interval   = 1.0 / NAV_POLL_HZ
        deadline   = time.perf_counter() + WAYPOINT_TIMEOUT_SEC
        prev_state = self._get_current_state()

        while self._running:
            if time.perf_counter() > deadline:
                print(f"[NAV]   timeout heading to ({tx:.0f}, {ty:.0f})")
                return "timeout"

            state = self._get_current_state()
            if state is None:
                return "ok"

            # ── respawn check ────────────────────────────────────────────────
            if self._spawn_position and prev_state:
                d_spawn = math.hypot(
                    state["x"] - self._spawn_position["x"],
                    state["y"] - self._spawn_position["y"],
                )
                self._max_dist_from_spawn = max(self._max_dist_from_spawn, d_spawn)

                jump = math.hypot(
                    state["x"] - prev_state["x"],
                    state["y"] - prev_state["y"],
                )
                # respawn = sudden teleport + landed near spawn
                if (self._max_dist_from_spawn > MOVED_THRESHOLD_PX
                        and jump > TELEPORT_THRESHOLD_PX
                        and d_spawn < RESPAWN_RADIUS_PX):
                    print(f"[NAV]   Respawn detected — "
                          f"jump={jump:.0f}px  "
                          f"dist_to_spawn={d_spawn:.0f}px  "
                          f"pos ({state['x']:.0f}, {state['y']:.0f})")
                    return "respawned"

            prev_state = state

            # ── move toward target ───────────────────────────────────────────
            dx   = tx - state["x"]
            dy   = ty - state["y"]
            dist = math.hypot(dx, dy)

            if dist <= threshold:
                return "ok"

            bearing = math.degrees(math.atan2(dx, -dy)) % 360
            self._rotate_to(bearing, state["rot"])
            self._step_forward(interval)
            time.sleep(interval)

        return "ok"


# ── entry point (identical to auto_run.py except uses OptimizedNavigator) ─────

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python record_replay/auto_run_optimized.py <snippet_file>")
        print("  e.g. python record_replay/auto_run_optimized.py assets/accomplish_snippet.json")
        sys.exit(1)

    snippet_path = sys.argv[1]
    if not Path(snippet_path).exists():
        print(f"[ERROR] Snippet file not found: {snippet_path}")
        sys.exit(1)

    waypoints = load_snippet(snippet_path)
    print(f"[AUTO] Loaded {len(waypoints)} waypoint(s) from {snippet_path}")

    print()
    print("=" * 55)
    print(f"  Snippet : {snippet_path}")
    print(f"  F8  → start as GR  (x=1901, y=123,  rot=270°)")
    print(f"  F9  → start as BL  (x=116,  y=261,  rot=90°)")
    print(f"  F10 → stop and save")
    print(f"  Respawn recovery : ON  (max {MAX_RESTARTS} restarts)")
    print(f"  Path re-route    : ON  (max {MAX_REROUTES} per waypoint)")
    print("=" * 55)

    # ── wait for team selection ───────────────────────────────────────────────
    team = start_state = None
    prev_f8 = prev_f9 = prev_f10 = False

    while team is None:
        f8  = _is_pressed(VK_F8)
        f9  = _is_pressed(VK_F9)
        f10 = _is_pressed(VK_F10)

        if f8 and not prev_f8:
            _wait_release(VK_F8)
            team, start_state = "GR", GR_SPAWN
        if f9 and not prev_f9:
            _wait_release(VK_F9)
            team, start_state = "BL", BL_SPAWN
        if f10 and not prev_f10:
            print("[AUTO] Quit")
            sys.exit(0)

        prev_f8, prev_f9, prev_f10 = f8, f9, f10
        time.sleep(0.01)

    print(f"[AUTO] Team: {team} — starting at "
          f"({start_state['x']:.0f}, {start_state['y']:.0f})  rot={start_state['rot']:.0f}°")
    _beep_countdown()

    session_id  = f"auto_opt_{team}_{int(time.time())}"
    output_path = f"record_replay/recordings/{session_id}.json"
    navigator   = OptimizedNavigator()

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
    print("[AUTO] Running — press F10 to stop and save at any time")

    # ── monitor F10 ───────────────────────────────────────────────────────────
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
