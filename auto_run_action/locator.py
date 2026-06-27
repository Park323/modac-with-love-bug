"""Thin wrapper around radar.locate() with a TTL cache.

Accepts a raw BGR frame (np.ndarray) and returns {x, y, rot} | None.
Caller provides the radar.py path so this module stays standalone.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

_LOCATE_TTL  = 0.10  # 10 Hz max
_DEFAULT_RADAR = Path(__file__).parent / "radar.py"


class Locator:
    def __init__(self, radar_path: str | Path | None = None) -> None:
        radar_path = radar_path or _DEFAULT_RADAR
        self._radar_path  = Path(radar_path)
        self._locate_fn: Callable | None = None
        self._cache_time  = 0.0
        self._cache_val: dict | None = None

    def _load(self) -> None:
        if self._locate_fn is not None:
            return
        spec = importlib.util.spec_from_file_location("_radar", self._radar_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._locate_fn = mod.locate

    def locate(self, frame: np.ndarray) -> dict | None:
        now = time.perf_counter()
        if now - self._cache_time < _LOCATE_TTL:
            return self._cache_val
        try:
            self._load()
            x, y, yaw, _ = self._locate_fn(frame)
            result: dict | None = {"x": float(x), "y": float(y), "rot": float(yaw)}
        except Exception as e:
            print(f"[LOC] locate failed: {e}")
            result = None
        self._cache_time = now
        self._cache_val  = result
        return result
