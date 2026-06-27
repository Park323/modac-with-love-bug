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
from .keys import NAME_TO_VK, scan_code_for_vk

# ── tuning constants ──────────────────────────────────────────────────────────

REACH_THRESHOLD_PX  = 30.0  # pixels — "close enough" to intermediate waypoint
FINAL_REACH_PX      = REACH_THRESHOLD_PX  # pixels — "close enough" to final waypoint
ROTATION_THRESH_DEG = 15.0   # degrees — "facing close enough"
MOUSE_PX_PER_DEGREE = 0.4  # 46px = 30° in-game
ROTATION_STEP_DEG   = 90.0    # max degrees to rotate per single call
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
        try:
            import cv2
            import numpy as np
            import importlib.util
            from pathlib import Path as _P

            if not hasattr(self, "_locate_fn"):
                _spec = importlib.util.spec_from_file_location(
                    "_radar", _P(__file__).parent.parent / "radar.py"
                )
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                self._locate_fn = _mod.locate

            if not hasattr(self, "_sct"):
                import mss as _mss
                self._sct = _mss.mss()

            raw = self._sct.grab(self._sct.monitors[1])
            frame = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
            x, y, yaw, _ = self._locate_fn(frame)
            return {"x": float(x), "y": float(y), "rot": float(yaw)}
        except Exception as e:
            print(f"[NAV] _get_current_state failed: {e}")
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
            path_pts = self._pathfinder.find_path(
                (state["x"], state["y"]),
                (target.x,   target.y),
            )
            print(f"[NAV]   path: {len(path_pts)} intermediate point(s)")
        else:
            path_pts = [(target.x, target.y)]

        scan_w = scan_code_for_vk(NAME_TO_VK["W"])
        wi.send_keyboard_scan(scan_w, False, is_up=False)
        try:
            for ix, (px, py) in enumerate(path_pts):
                if not self._running:
                    return
                is_final  = (ix == len(path_pts) - 1)
                threshold = FINAL_REACH_PX if is_final else REACH_THRESHOLD_PX
                self._walk_to(px, py, threshold)
        finally:
            wi.send_keyboard_scan(scan_w, False, is_up=True)


    def _walk_to(self, tx: float, ty: float, threshold: float) -> None:
        """Steer toward (tx, ty) and wait until within threshold. W must already be held."""
        deadline = time.perf_counter() + WAYPOINT_TIMEOUT_SEC

        # Rotate to segment bearing once before walking
        init = getattr(self, "_last_known_state", None)
        if init:
            dx0 = tx - init["x"]
            dy0 = ty - init["y"]
            if math.hypot(dx0, dy0) > threshold:
                self._rotate_to(math.degrees(math.atan2(dx0, -dy0)) % 360, init["rot"])

        while self._running:
            if time.perf_counter() > deadline:
                print(f"[NAV]   timeout — could not reach ({tx:.0f}, {ty:.0f}), moving on")
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
            print(f"[NAV]   pos=({state['x']:.0f},{state['y']:.0f}) rot={state['rot']:.0f}° "
                  f"→ target=({tx:.0f},{ty:.0f}) dist={dist:.0f}px  action=FORWARD")
            if dist <= threshold:
                return

            bearing = math.degrees(math.atan2(tx - state["x"], -(ty - state["y"]))) % 360
            self._rotate_to(bearing, state["rot"])

    def _rotate_to(self, target_rot: float, current_rot: float) -> None:
        delta = (target_rot - current_rot + 180) % 360 - 180
        if abs(delta) < ROTATION_THRESH_DEG:
            return
        step     = math.copysign(min(abs(delta), ROTATION_STEP_DEG), delta)
        mouse_dx = round(step * MOUSE_PX_PER_DEGREE)
        print(f"[NAV]   rot={current_rot:.0f}° → target={target_rot:.0f}°  "
              f"delta={delta:+.1f}°  step={step:+.1f}°  mouse_dx={mouse_dx:+d}px  action=ROTATE")
        wi.send_mouse_relative(mouse_dx, 0)

    def _step_forward(self, duration: float) -> None:
        scan_w = scan_code_for_vk(NAME_TO_VK["W"])
        wi.send_keyboard_scan(scan_w, False, is_up=False)
        time.sleep(duration)
        wi.send_keyboard_scan(scan_w, False, is_up=True)
