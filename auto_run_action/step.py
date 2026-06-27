"""next_event: given current position and a target waypoint, return the single
next input event to execute. Returns None when the waypoint is reached.

The caller (manager) runs the loop — call this each tick, execute what comes back.
"""

from __future__ import annotations

import math

REACH_THRESHOLD_PX  = 100.0   # arrived when within this distance
ROTATION_THRESH_DEG = 15.0    # ignore bearing error smaller than this
MOUSE_PX_PER_DEGREE = 46      # tune to match in-game sensitivity


def next_event(
    position: dict,   # {"x": float, "y": float, "rot": float}
    waypoint: dict,   # {"x": float, "y": float}
) -> dict | None:
    """
    Returns the next input event to send, or None if the waypoint is reached.

    Possible return values:
      {"type": "mouse_move", "dx": int, "dy": 0}   — rotate toward waypoint
      {"type": "key_down",   "key": "W", "scan": 17, "extended": False}  — move forward
      None  — arrived
    """
    dx   = waypoint["x"] - position["x"]
    dy   = waypoint["y"] - position["y"]
    dist = math.hypot(dx, dy)

    if dist <= REACH_THRESHOLD_PX:
        return None

    bearing = math.degrees(math.atan2(dx, -dy)) % 360
    delta   = (bearing - position["rot"] + 180) % 360 - 180

    if abs(delta) > ROTATION_THRESH_DEG:
        # cap at 30° per tick to avoid overshooting
        clamped = max(-30.0, min(30.0, delta))
        mouse_dx = round(clamped * MOUSE_PX_PER_DEGREE)
        return {"type": "mouse_move", "dx": mouse_dx, "dy": 0}

    return {"type": "key_down", "key": "W", "scan": 17, "extended": False}
