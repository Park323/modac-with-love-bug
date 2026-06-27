"""AutoRunAction — manager-facing entry point for autonomous navigation.

Inputs (via start()):
  waypoints  : list[dict]            — snippet items [{idx, x, y, rot}, ...]
  get_frame  : Callable[[], ndarray] — latest BGR screen frame from manager
  output_path: str                   — where to save the recorded JSON

Usage:
    action = AutoRunAction()
    action.start(waypoints, get_frame=capture.latest_frame, output_path="...")
    action.stop()
    action.status()  # -> {state, wp_index, total, elapsed_sec}
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .locator import Locator
from .navigator import OptimizedNavigator, Waypoint

_DEFAULT_RADAR_PATH = Path(__file__).parent / "radar.py"
_DEFAULT_MAP_PATH   = "assets/mapinfo.json"


class AutoRunAction:
    def __init__(
        self,
        radar_path:   str | Path | None = None,
        mapinfo_path: str = _DEFAULT_MAP_PATH,
    ) -> None:
        self._radar_path   = Path(radar_path) if radar_path else _DEFAULT_RADAR_PATH
        self._mapinfo_path = mapinfo_path
        self._navigator: OptimizedNavigator | None = None
        self._thread:    threading.Thread   | None = None
        self._lock       = threading.Lock()
        self._state      = "idle"
        self._wp_index   = 0
        self._total      = 0
        self._start_time = 0.0
        self._error: str | None = None

    # ── public ────────────────────────────────────────────────────────────────

    def start(
        self,
        waypoints:   list[dict],
        get_frame:   Callable[[], np.ndarray],
        output_path: str,
        session_id:  str | None = None,
    ) -> None:
        with self._lock:
            if self._state == "running":
                raise RuntimeError("already running")
            self._state = "running"

        locator = Locator(self._radar_path)
        nav = OptimizedNavigator(
            get_frame=get_frame,
            locator=locator,
            mapinfo_path=self._mapinfo_path,
        )

        sorted_wps = sorted(waypoints, key=lambda w: w.get("idx", 0))
        wps = [Waypoint.from_dict(w) for w in sorted_wps]

        session_id  = session_id or f"auto_run_{int(time.time())}"
        self._navigator  = nav
        self._total      = len(wps)
        self._start_time = time.perf_counter()
        self._wp_index   = 0
        self._error      = None

        self._thread = threading.Thread(
            target=self._run,
            args=(nav, wps, output_path, session_id),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._navigator:
            self._navigator.stop()
        with self._lock:
            if self._state == "running":
                self._state = "stopped"

    def status(self) -> dict:
        with self._lock:
            elapsed = (time.perf_counter() - self._start_time
                       if self._state == "running" else 0.0)
            return {
                "state":       self._state,
                "wp_index":    self._wp_index,
                "total":       self._total,
                "elapsed_sec": round(elapsed, 1),
                "error":       self._error,
            }

    # ── internal ──────────────────────────────────────────────────────────────

    def _run(self, nav: OptimizedNavigator, wps: list[Waypoint],
             output_path: str, session_id: str) -> None:
        try:
            nav.run(wps, output_path, session_id)
            with self._lock:
                if self._state == "running":
                    self._state = "done"
        except Exception as e:
            with self._lock:
                self._state = "error"
                self._error = str(e)
