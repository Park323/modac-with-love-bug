"""AutoNavigator + OptimizedNavigator for auto_run_action.

Adapted from record_replay/src/navigator.py and record_replay/auto_run_optimized.py.
Key difference: _get_current_state() uses an injected get_frame callback + Locator
instead of capturing the screen itself.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .recorder import HookRecorder
from .pathfinder import MapPathfinder
from .locator import Locator
from . import win_input as wi
from .keys import NAME_TO_VK, scan_code_for_vk

# ── tuning ────────────────────────────────────────────────────────────────────

REACH_THRESHOLD_PX   = 50.0
FINAL_REACH_PX       = 50.0
ROTATION_THRESH_DEG  = 15.0
MOUSE_PX_PER_DEGREE  = 46
ROTATE_STEPS         = 10
ROTATE_STEP_SEC      = 0.005
NAV_POLL_HZ          = 10
WAYPOINT_TIMEOUT_SEC = 30.0

RESPAWN_RADIUS_PX     = 80.0
MOVED_THRESHOLD_PX    = 150.0
TELEPORT_THRESHOLD_PX = 200.0
MAX_RESTARTS          = 5
MAX_RECORDING_SEC     = 30.0
MAX_REROUTES          = 5
RESPAWN_SETTLE_MAX_SEC = 8.0
RESPAWN_SETTLE_DIST_PX = 5.0
RESPAWN_SETTLE_POLLS   = 5


# ── data ──────────────────────────────────────────────────────────────────────

@dataclass
class Waypoint:
    x:   float
    y:   float
    rot: float

    @classmethod
    def from_dict(cls, d: dict) -> "Waypoint":
        pos = d.get("position") or d
        return cls(x=float(pos["x"]), y=float(pos["y"]), rot=float(pos["rot"]))


# ── base navigator ────────────────────────────────────────────────────────────

class AutoNavigator:
    def __init__(
        self,
        get_frame:    Callable[[], np.ndarray],
        locator:      Locator,
        mapinfo_path: str = "assets/mapinfo.json",
    ) -> None:
        self._get_frame  = get_frame
        self._locator    = locator
        self._recorder   = HookRecorder(
            get_position_fn=self._get_position_for_recorder
        )
        self._record_thread: threading.Thread | None = None
        self._running    = False

        if Path(mapinfo_path).exists():
            self._pathfinder: MapPathfinder | None = MapPathfinder(mapinfo_path)
            print(f"[NAV] Pathfinder loaded from {mapinfo_path}")
        else:
            self._pathfinder = None
            print(f"[NAV] {mapinfo_path} not found — straight-line navigation only")

    # ── public ────────────────────────────────────────────────────────────────

    def run(self, waypoints: list[Waypoint], output_path: str,
            session_id: str = "auto_run") -> dict:
        self._running = True
        self._start_recording()
        try:
            for i, wp in enumerate(waypoints):
                if not self._running:
                    break
                print(f"[NAV] Waypoint {i+1}/{len(waypoints)}: ({wp.x:.0f}, {wp.y:.0f})")
                self._navigate_to(wp)
            if self._running:
                print("[NAV] Mission complete")
            time.sleep(0.3)
        finally:
            self._running = False
            result = self._stop_recording(output_path, session_id)
            print(f"[NAV] Saved → {output_path}  "
                  f"({result['session']['event_count']} events, "
                  f"{result['session']['duration_sec']}s)")
        return result

    def stop(self) -> None:
        self._running = False

    # ── recording lifecycle ───────────────────────────────────────────────────

    def _start_recording(self) -> None:
        self._recorder.events = []
        self._record_thread = threading.Thread(target=self._recorder.start, daemon=True)
        self._record_thread.start()

    def _stop_recording(self, output_path: str, session_id: str) -> dict:
        self._recorder.stop()
        if self._record_thread:
            self._record_thread.join(timeout=1.0)
        return self._recorder.save(output_path, session_id)

    # ── position sensing ──────────────────────────────────────────────────────

    def _get_current_state(self) -> dict | None:
        try:
            frame = self._get_frame()
            return self._locator.locate(frame)
        except Exception as e:
            print(f"[NAV] _get_current_state failed: {e}")
            return None

    def _get_position_for_recorder(self) -> dict | None:
        try:
            frame = self._get_frame()
            return self._locator.locate(frame)
        except Exception:
            return None

    # ── navigation ────────────────────────────────────────────────────────────

    def _navigate_to(self, target: Waypoint) -> None:
        raw = self._get_current_state()
        if raw is not None:
            self._last_known_state = raw
            state = raw
        elif getattr(self, "_last_known_state", None):
            state = self._last_known_state
        else:
            print("[NAV]   No position data — waypoint skipped")
            return

        if self._pathfinder is not None:
            path_pts = self._pathfinder.find_path((state["x"], state["y"]),
                                                   (target.x,  target.y))
            print(f"[NAV]   path: {len(path_pts)} point(s)")
        else:
            path_pts = [(target.x, target.y)]

        scan_w = scan_code_for_vk(NAME_TO_VK["W"])
        wi.send_keyboard_scan(scan_w, False, is_up=False)
        try:
            for ix, (px, py) in enumerate(path_pts):
                if not self._running:
                    return
                threshold = FINAL_REACH_PX if ix == len(path_pts) - 1 else REACH_THRESHOLD_PX
                self._walk_to(px, py, threshold)
        finally:
            wi.send_keyboard_scan(scan_w, False, is_up=True)

    def _walk_to(self, tx: float, ty: float, threshold: float) -> None:
        """Steer toward (tx, ty). W must already be held by caller."""
        deadline = time.perf_counter() + WAYPOINT_TIMEOUT_SEC

        init = getattr(self, "_last_known_state", None)
        if init:
            dx0, dy0 = tx - init["x"], ty - init["y"]
            if math.hypot(dx0, dy0) > threshold:
                self._rotate_to(math.degrees(math.atan2(dx0, -dy0)) % 360, init["rot"])

        while self._running:
            if time.perf_counter() > deadline:
                print(f"[NAV]   timeout → ({tx:.0f}, {ty:.0f})")
                return
            raw = self._get_current_state()
            if raw is not None:
                self._last_known_state = raw
                state = raw
            elif getattr(self, "_last_known_state", None):
                state = self._last_known_state
            else:
                time.sleep(0.05)
                continue

            dist = math.hypot(tx - state["x"], ty - state["y"])
            if dist <= threshold:
                return

            bearing = math.degrees(math.atan2(tx - state["x"], -(ty - state["y"]))) % 360
            self._rotate_to(bearing, state["rot"])

    def _rotate_to(self, target_rot: float, current_rot: float) -> None:
        delta = (target_rot - current_rot + 180) % 360 - 180
        if abs(delta) < ROTATION_THRESH_DEG:
            return
        total_dx = delta * MOUSE_PX_PER_DEGREE
        step_dx  = total_dx / ROTATE_STEPS
        for _ in range(ROTATE_STEPS):
            wi.send_mouse_relative(round(step_dx), 0)
            time.sleep(ROTATE_STEP_SEC)


# ── optimized navigator (respawn recovery + reroute) ─────────────────────────

class OptimizedNavigator(AutoNavigator):

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._spawn_position:      dict | None = None
        self._max_dist_from_spawn: float       = 0.0

    def run(self, waypoints: list[Waypoint], output_path: str,
            session_id: str = "auto_run_opt") -> dict:
        self._spawn_position = self._get_current_state()
        if self._spawn_position:
            print(f"[NAV] Spawn: ({self._spawn_position['x']:.0f}, "
                  f"{self._spawn_position['y']:.0f})")
        else:
            print("[NAV] Spawn not detected — respawn recovery disabled")
        self._max_dist_from_spawn = 0.0
        self._running = True
        self._start_recording()
        try:
            self._run_loop(waypoints)
            time.sleep(0.3)
        finally:
            self._running = False
            result = self._stop_recording(output_path, session_id)
            print(f"[NAV] Saved → {output_path}  "
                  f"({result['session']['event_count']} events, "
                  f"{result['session']['duration_sec']}s)")
        return result

    # ── main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self, waypoints: list[Waypoint]) -> None:
        wp_index      = 0
        restart_count = 0
        deadline      = time.perf_counter() + MAX_RECORDING_SEC

        while wp_index < len(waypoints) and self._running:
            if time.perf_counter() > deadline:
                print(f"[NAV] Max recording time ({MAX_RECORDING_SEC}s) — stopping")
                self._running = False
                return

            wp = waypoints[wp_index]
            print(f"[NAV] Waypoint {wp_index+1}/{len(waypoints)}: "
                  f"({wp.x:.0f}, {wp.y:.0f})")

            signal = self._navigate_to_optimized(wp)

            if signal == "respawned":
                restart_count += 1
                if restart_count > MAX_RESTARTS:
                    print(f"[NAV] Respawn limit ({MAX_RESTARTS}) — aborting")
                    self._running = False
                    return
                print(f"[NAV] Respawn #{restart_count} — settling")
                self._max_dist_from_spawn = 0.0
                self._wait_for_respawn_settle()
                wp_index = 0
                continue

            wp_index += 1

        if self._running:
            print("[NAV] Mission complete")

    # ── optimized navigate ────────────────────────────────────────────────────

    def _navigate_to_optimized(self, target: Waypoint) -> str:
        for attempt in range(MAX_REROUTES + 1):
            raw = self._get_current_state()
            if raw is not None:
                self._last_known_state = raw
                state = raw
            elif getattr(self, "_last_known_state", None):
                state = self._last_known_state
            else:
                print("[NAV]   No position data — skipping waypoint")
                return "ok"

            path_pts = (self._pathfinder.find_path((state["x"], state["y"]),
                                                    (target.x,  target.y))
                        if self._pathfinder else [(target.x, target.y)])

            if attempt > 0:
                print(f"[NAV]   Re-route #{attempt}: {len(path_pts)} point(s)")

            needs_reroute = False
            scan_w = scan_code_for_vk(NAME_TO_VK["W"])
            wi.send_keyboard_scan(scan_w, False, is_up=False)
            try:
                for ix, (px, py) in enumerate(path_pts):
                    if not self._running:
                        return "ok"
                    threshold = FINAL_REACH_PX if ix == len(path_pts) - 1 else REACH_THRESHOLD_PX
                    signal = self._walk_to_optimized(px, py, threshold)
                    if signal == "respawned":
                        return "respawned"
                    if signal == "timeout":
                        needs_reroute = True
                        break
            finally:
                wi.send_keyboard_scan(scan_w, False, is_up=True)

            if not needs_reroute:
                return "ok"

        print(f"[NAV]   Could not reach waypoint after {MAX_REROUTES} re-routes")
        return "ok"

    def _walk_to_optimized(self, tx: float, ty: float, threshold: float) -> str:
        """Steer toward (tx, ty). W must already be held by caller."""
        deadline   = time.perf_counter() + WAYPOINT_TIMEOUT_SEC
        prev_state = (self._get_current_state()
                      or getattr(self, "_last_known_state", None))
        if prev_state:
            self._last_known_state = prev_state

        if prev_state:
            dx0, dy0 = tx - prev_state["x"], ty - prev_state["y"]
            if math.hypot(dx0, dy0) > threshold:
                self._rotate_to(
                    math.degrees(math.atan2(dx0, -dy0)) % 360,
                    prev_state["rot"],
                )

        while self._running:
            if time.perf_counter() > deadline:
                print(f"[NAV]   timeout → ({tx:.0f}, {ty:.0f})")
                return "timeout"

            raw = self._get_current_state()
            if raw is not None:
                self._last_known_state = raw
                state = raw
            elif getattr(self, "_last_known_state", None):
                state = self._last_known_state
            else:
                time.sleep(0.05)
                continue

            if self._spawn_position and prev_state:
                d_spawn = math.hypot(state["x"] - self._spawn_position["x"],
                                     state["y"] - self._spawn_position["y"])
                self._max_dist_from_spawn = max(self._max_dist_from_spawn, d_spawn)
                jump = math.hypot(state["x"] - prev_state["x"],
                                  state["y"] - prev_state["y"])
                if (self._max_dist_from_spawn > MOVED_THRESHOLD_PX
                        and jump > TELEPORT_THRESHOLD_PX
                        and d_spawn < RESPAWN_RADIUS_PX):
                    print(f"[NAV]   Respawn detected — jump={jump:.0f}px")
                    return "respawned"

            prev_state = state

            dist = math.hypot(tx - state["x"], ty - state["y"])
            if dist <= threshold:
                return "ok"

            bearing = math.degrees(math.atan2(tx - state["x"], -(ty - state["y"]))) % 360
            self._rotate_to(bearing, state["rot"])

        return "ok"

    def _wait_for_respawn_settle(self) -> None:
        deadline     = time.perf_counter() + RESPAWN_SETTLE_MAX_SEC
        stable_count = 0
        prev = self._get_current_state() or getattr(self, "_last_known_state", None)

        while time.perf_counter() < deadline and self._running:
            time.sleep(0.1)
            curr = self._get_current_state() or getattr(self, "_last_known_state", None)
            if prev is None:
                prev = curr
                continue
            if math.hypot(curr["x"] - prev["x"], curr["y"] - prev["y"]) < RESPAWN_SETTLE_DIST_PX:
                stable_count += 1
                if stable_count >= RESPAWN_SETTLE_POLLS:
                    print(f"[NAV]   Settled at ({curr['x']:.0f}, {curr['y']:.0f})")
                    return
            else:
                stable_count = 0
            prev = curr

        print("[NAV]   Respawn settle timeout — proceeding")
