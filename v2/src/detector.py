"""
Screen detector: template matching + RMSD pixel comparison.
Inspired by the screen-reading approach (no hooks, purely external).
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import mss
import numpy as np


class ScreenDetector:
    def __init__(self, templates_dir: str = "templates", threshold: float = 0.80) -> None:
        self._templates_dir = Path(templates_dir)
        self._threshold = threshold
        self._cache: dict[str, np.ndarray] = {}

    # ── screen capture ────────────────────────────────────────────────────────

    def capture(self) -> np.ndarray:
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
        return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)

    def capture_region(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        with mss.mss() as sct:
            raw = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)

    # ── RMSD comparison (from snippet approach) ───────────────────────────────

    @staticmethod
    def rmsd(img_a: np.ndarray, img_b: np.ndarray) -> float:
        """Root mean square deviation between two same-shape images."""
        diff = img_a.astype(np.int32) - img_b.astype(np.int32)
        return float(np.sqrt(np.mean(diff ** 2)))

    def screen_changed(self, prev: np.ndarray, curr: np.ndarray, threshold: float = 10.0) -> bool:
        return self.rmsd(prev, curr) > threshold

    # ── template matching ─────────────────────────────────────────────────────

    def match(self, screen: np.ndarray, template_name: str) -> float:
        tmpl = self._load(template_name)
        if tmpl is None:
            return 0.0
        result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
        return float(result.max())

    def wait_for(self, template_name: str, timeout_sec: float = 60.0, poll_sec: float = 0.5) -> bool:
        deadline = time.perf_counter() + timeout_sec
        while time.perf_counter() < deadline:
            score = self.match(self.capture(), template_name)
            if score >= self._threshold:
                return True
            time.sleep(poll_sec)
        return False

    # ── template management ───────────────────────────────────────────────────

    def save_screenshot(self, name: str) -> str:
        self._templates_dir.mkdir(parents=True, exist_ok=True)
        path = str(self._templates_dir / f"{name}.png")
        cv2.imwrite(path, self.capture())
        return path

    def list_templates(self) -> list[str]:
        if not self._templates_dir.exists():
            return []
        return [p.stem for p in self._templates_dir.glob("*.png")]

    def _load(self, name: str) -> np.ndarray | None:
        if name not in self._cache:
            p = self._templates_dir / f"{name}.png"
            if not p.exists():
                return None
            self._cache[name] = cv2.imread(str(p))
        return self._cache[name]
