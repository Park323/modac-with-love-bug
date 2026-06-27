"""Wraps OptimizedNavigator for use inside test_auto_run_executor.

Handles:
  - client waypoint format → Waypoint objects
  - team → spawn position
  - thread lifecycle (start / stop / status)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

# make record_replay importable from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from record_replay.auto_run_optimized import OptimizedNavigator
from record_replay.src.navigator import Waypoint

BL_SPAWN = {"x": 116.0,  "y": 261.0, "rot": 90.0}
GR_SPAWN = {"x": 1901.0, "y": 123.0, "rot": 270.0}

SPAWN_BY_TEAM = {"BL": BL_SPAWN, "GR": GR_SPAWN}


def parse_client_waypoints(raw: list[dict[str, Any]]) -> list[Waypoint]:
    """Convert client format [{idx, x, y, rot}] → [Waypoint]."""
    sorted_raw = sorted(raw, key=lambda w: w.get("idx", 0))
    return [
        Waypoint(x=float(w["x"]), y=float(w["y"]), rot=float(w["rot"]))
        for w in sorted_raw
    ]


class AutoRunSession:
    """Manages one auto-run lifecycle (start → running → done/stopped)."""

    def __init__(self) -> None:
        self._navigator: OptimizedNavigator | None = None
        self._thread:    threading.Thread | None   = None
        self._status:    str                       = "idle"
        self._error:     str | None                = None
        self._session_id: str | None               = None

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        if self._thread and not self._thread.is_alive() and self._status == "running":
            self._status = "done"
        return self._status

    @property
    def is_running(self) -> bool:
        return self._status == "running" and bool(self._thread and self._thread.is_alive())

    def start(
        self,
        waypoints:   list[Waypoint],
        output_path: str,
        session_id:  str,
        team:        str,
    ) -> None:
        if self.is_running:
            raise RuntimeError("Auto-run is already running")

        spawn = SPAWN_BY_TEAM.get(team.upper())
        if spawn is None:
            raise ValueError(f"Unknown team '{team}'. Use 'BL' or 'GR'.")

        self._navigator  = OptimizedNavigator()
        self._session_id = session_id
        self._status     = "running"
        self._error      = None

        def _run() -> None:
            try:
                self._navigator.run(  # type: ignore[union-attr]
                    waypoints=waypoints,
                    output_path=output_path,
                    session_id=session_id,
                    start_state=spawn,
                )
                self._status = "done"
            except Exception as exc:
                self._status = "error"
                self._error  = str(exc)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._navigator:
            self._navigator.stop()
        self._status = "stopped"

    def summary(self) -> dict[str, Any]:
        return {
            "status":     self.status,
            "session_id": self._session_id,
            "error":      self._error,
        }
