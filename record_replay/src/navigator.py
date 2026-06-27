"""
AutoNavigator: drives the character through a sequence of position waypoints
while recording all inputs. Uses A* pathfinding (via MapPathfinder) to route
around obstacles defined in mapinfo.json.

Only stub remaining:
  _get_current_state() → fill in with teammate's real-time position module.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .recorder import PollingRecorder
from .pathfinder import MapPathfinder
from . import win_input as wi
from .keys import NAME_TO_VK

# ── tuning constants ──────────────────────────────────────────────────────────

REACH_THRESHOLD_PX  = 15.0   # pixels — "close enough" to intermediate waypoint
FINAL_REACH_PX      = 20.0   # pixels — "close enough" to final waypoint
ROTATION_THRESH_DEG = 5.0    # degrees — "facing close enough"
MOUSE_PX_PER_DEGREE = 0.5    # tune to match in-game sensitivity
NAV_POLL_HZ         = 10     # position re-check rate while navigating
WAYPOINT_TIMEOUT_SEC = 30.0  # max seconds to spend trying to reach one waypoint


# ── data ──────────────────────────────────────────────────────────────────────

@dataclass
class Waypoint:
    x:   float
    y:   float
    rot: float   # target facing direction (degrees, 0=north/up, 90=east, clockwise)

    @classmethod
    def from_dict(cls, d: dict) -> "Waypoint":
        pos = d.get("position") or d
        return cls(x=float(pos["x"]), y=float(pos["y"]), rot=float(pos["rot"]))


# ── navigator ─────────────────────────────────────────────────────────────────

class AutoNavigator:
    def __init__(
        self,
        recorder:     PollingRecorder | None = None,
        mapinfo_path: str = "assets/mapinfo.json",
    ) -> None:
        self._recorder      = recorder or PollingRecorder(sample_hz=120)
        self._record_thread: threading.Thread | None = None
        self._running       = False

        if Path(mapinfo_path).exists():
            self._pathfinder: MapPathfinder | None = MapPathfinder(mapinfo_path)
            print(f"[NAV] Pathfinder loaded from {mapinfo_path}")
        else:
            self._pathfinder = None
            print(f"[NAV] {mapinfo_path} not found — straight-line navigation only")

    # ── public ────────────────────────────────────────────────────────────────

    def run(
        self,
        waypoints:   list[Waypoint],
        output_path: str,
        session_id:  str = "auto_run",
        start_state: dict | None = None,
    ) -> dict:
        """Navigate through all waypoints while recording, then save."""
        self._start_state = start_state
        self._running     = True
        self._start_recording()
        try:
            for i, wp in enumerate(waypoints):
                if not self._running:
                    break
                print(f"[NAV] Waypoint {i + 1}/{len(waypoints)}: "
                      f"({wp.x:.0f}, {wp.y:.0f})  rot={wp.rot:.0f}°")
                self._navigate_to(wp)

            if self._running:
                print("[NAV] Mission complete — all waypoints reached")
            else:
                print("[NAV] Stopped early by user")
            time.sleep(0.3)
        finally:
            self._running = False
            result = self._stop_recording(output_path, session_id)
            print(f"[NAV] Recording saved → {output_path}"
                  f"  ({result['session']['event_count']} events,"
                  f"  {result['session']['duration_sec']}s)")
        return result

    def stop(self) -> None:
        """Signal the navigator to stop after the current step and save."""
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

    # ── position sensing (stub) ───────────────────────────────────────────────

    def _get_current_state(self) -> dict[str, float] | None:
        # TODO: replace with teammate's real-time position module.
        # Expected return: {"x": float, "y": float, "rot": float}
        # Coordinates in the same pixel space as mapinfo.json (origin top-left).
        #
        # _start_state is used as the initial position until real tracking is ready.
        return getattr(self, "_start_state", None)

    # ── navigation ────────────────────────────────────────────────────────────

    def _navigate_to(self, target: Waypoint) -> None:
        state = self._get_current_state()
        if state is None:
            print("[NAV]   _get_current_state() not implemented — waypoint skipped")
            return

        if self._pathfinder is not None:
            path_pts = self._pathfinder.find_path(
                (state["x"], state["y"]),
                (target.x,   target.y),
            )
            print(f"[NAV]   path: {len(path_pts)} intermediate point(s)")
        else:
            path_pts = [(target.x, target.y)]

        for ix, (px, py) in enumerate(path_pts):
            if not self._running:
                return
            is_final  = (ix == len(path_pts) - 1)
            threshold = FINAL_REACH_PX if is_final else REACH_THRESHOLD_PX
            self._walk_to(px, py, threshold)

        state = self._get_current_state()
        if state and self._running:
            self._rotate_to(target.rot, state["rot"])

    def _walk_to(self, tx: float, ty: float, threshold: float) -> None:
        """Move toward (tx, ty) until within threshold pixels or timeout."""
        interval = 1.0 / NAV_POLL_HZ
        deadline = time.perf_counter() + WAYPOINT_TIMEOUT_SEC

        while self._running:
            if time.perf_counter() > deadline:
                print(f"[NAV]   timeout — could not reach ({tx:.0f}, {ty:.0f}), moving on")
                return

            state = self._get_current_state()
            if state is None:
                return

            dx   = tx - state["x"]
            dy   = ty - state["y"]
            dist = math.hypot(dx, dy)

            if dist <= threshold:
                return

            bearing = math.degrees(math.atan2(dx, -dy)) % 360
            self._rotate_to(bearing, state["rot"])
            self._step_forward(interval)
            time.sleep(interval)

    def _rotate_to(self, target_rot: float, current_rot: float) -> None:
        delta = (target_rot - current_rot + 180) % 360 - 180
        if abs(delta) < ROTATION_THRESH_DEG:
            return
        mouse_dx = int(delta * MOUSE_PX_PER_DEGREE)
        wi.send_mouse_relative(mouse_dx, 0)
        time.sleep(0.03)

    def _step_forward(self, duration: float) -> None:
        vk_w = NAME_TO_VK["W"]
        wi.send_keyboard_vk(vk_w, is_up=False)
        time.sleep(duration)
        wi.send_keyboard_vk(vk_w, is_up=True)
