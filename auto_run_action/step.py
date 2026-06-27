"""next_event: given current position and a target waypoint, return the single
next input event to execute. Returns None when the waypoint is reached.

The caller (manager) runs the loop — call this each tick, execute what comes back.
"""

from __future__ import annotations

import math
from .position import get_position

REACH_THRESHOLD_PX  = 50.0   # arrived when within this distance
ROTATION_THRESH_DEG = 15.0    # ignore bearing error smaller than this
MOUSE_PX_PER_DEGREE = 0.4  # 46px = 30° in-game
ROTATION_STEP_DEG   = 90.0    # max degrees to rotate per single event
WAYPOINTS = []


def _is_reached(position: dict, waypoint: dict) -> bool:
    """Return True if the current position is close enough to the waypoint."""
    dx   = waypoint["x"] - position["x"]
    dy   = waypoint["y"] - position["y"]
    dist = math.hypot(dx, dy)
    return dist <= REACH_THRESHOLD_PX

def _get_cur_waypoint() -> dict | None:
    """Return the current waypoint (first in list) or None if no waypoints remain."""
    return WAYPOINTS[0] if len(WAYPOINTS) > 0 else None

def next_event(
    position: dict,   # {"x": float, "y": float, "rot": float}
    waypoints: dict = None,   # [{"idx": int, "x": float, "y": float, "rot": int }, ...]
) -> dict | None:
    """
    Returns the next input event to send, or None if the waypoint is reached.

    Possible return values:
      {"type": "mouse_move", "dx": int, "dy": 0}   — rotate toward waypoint
      {"type": "key_down",   "key": "W", "scan": 17, "extended": False}  — move forward
      None  — arrived
    """
    if waypoints:
        global WAYPOINTS
        WAYPOINTS = waypoints

    current_waypoint = _get_cur_waypoint()
    if not current_waypoint:
        return None

    if _is_reached(position, current_waypoint):
        WAYPOINTS.pop(0)
        current_waypoint = _get_cur_waypoint()
        if not current_waypoint:
            return None

    bearing = math.degrees(math.atan2(current_waypoint["x"] - position["x"], -(current_waypoint["y"] - position["y"]))) % 360
    delta   = (bearing - position["rot"] + 180) % 360 - 180

    if abs(delta) > ROTATION_THRESH_DEG:
        step = math.copysign(min(abs(delta), ROTATION_STEP_DEG), delta)
        mouse_dx = round(step * MOUSE_PX_PER_DEGREE)
        return {"type": "mouse_move", "dx": mouse_dx, "dy": 0}

    return {"type": "key_down", "key": "W", "scan": 17, "extended": False}


def get_event(
    frame: dict,   # np.ndarray, BGR full-screen capture
    waypoints: dict = None,   # [{"idx": int, "x": float, "y": float, "rot": int }, ...]
) -> dict | None:
    """
    Returns the next input event to send, or None if the waypoint is reached.
    This is a wrapper around next_event() that also updates the global WAYPOINTS list.
    """
    position = get_position(frame)
    return next_event(position, waypoints)
