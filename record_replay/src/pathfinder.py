"""
Grid-based A* pathfinder using mapinfo.json obstacle data.

Builds a walkability bitmap from:
  walls[0].holes[0]  → walkable region (inside the hole = walkable)
  objects[*].polygon → impassable obstacles (with pixel margin for character width)

find_path(start, end) → smoothed list of (x, y) pixel waypoints that avoid obstacles.
"""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

GRID_SCALE   = 4    # 1 grid cell = 4 pixels  →  1980×654 becomes 495×163
OBS_MARGIN   = 12   # pixel margin dilated around each obstacle (character half-width)


class MapPathfinder:
    def __init__(self, mapinfo_path: str = "assets/mapinfo.json") -> None:
        with open(mapinfo_path, encoding="utf-8") as f:
            info = json.load(f)

        self._W   = info["size"]["width"]
        self._H   = info["size"]["height"]
        self._gw  = self._W // GRID_SCALE
        self._gh  = self._H // GRID_SCALE
        self._grid = self._build(info)   # bool array [gh, gw], True = walkable

    # ── public ────────────────────────────────────────────────────────────────

    def find_path(
        self,
        start: tuple[float, float],
        end:   tuple[float, float],
    ) -> list[tuple[float, float]]:
        """
        Return a list of pixel (x, y) waypoints from start to end, routing
        around obstacles. Falls back to [end] if no path is found.
        """
        gs = self._to_grid(start)
        ge = self._to_grid(end)

        raw = self._astar(gs, ge)
        if raw is None:
            return [end]

        pixel_path = [self._to_pixel(g) for g in raw]
        return self._smooth(pixel_path)

    # ── grid construction ─────────────────────────────────────────────────────

    def _build(self, info: dict) -> np.ndarray:
        full = np.zeros((self._H, self._W), dtype=np.uint8)

        # walkable interior = inside the hole polygon
        for wall in info.get("walls", []):
            for hole in wall.get("holes", []):
                pts = np.array(hole, dtype=np.int32)
                cv2.fillPoly(full, [pts], 255)

        # carve out obstacles (dilated for character margin)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (OBS_MARGIN * 2 + 1, OBS_MARGIN * 2 + 1)
        )
        for obj in info.get("objects", []):
            mask = np.zeros((self._H, self._W), dtype=np.uint8)
            pts  = np.array(obj["polygon"], dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
            mask = cv2.dilate(mask, kernel)
            full[mask > 0] = 0

        # also carve out the solid wall band itself (keep only hole interior)
        for wall in info.get("walls", []):
            outer = np.array(wall["polygon"], dtype=np.int32)
            outer_mask = np.zeros((self._H, self._W), dtype=np.uint8)
            cv2.fillPoly(outer_mask, [outer], 255)
            # pixels inside outer but not walkable from hole fill → already 0, keep full as-is

        small = cv2.resize(full, (self._gw, self._gh), interpolation=cv2.INTER_NEAREST)
        return small > 127

    # ── A* ────────────────────────────────────────────────────────────────────

    def _astar(
        self,
        start: tuple[int, int],
        end:   tuple[int, int],
    ) -> Optional[list[tuple[int, int]]]:
        if not self._walkable(*start):
            start = self._nearest_walkable(start)
        if not self._walkable(*end):
            end = self._nearest_walkable(end)
        if start is None or end is None:
            return None

        def h(a: tuple, b: tuple) -> float:
            return math.hypot(a[0] - b[0], a[1] - b[1])

        open_set: list = [(0.0, start)]
        came_from: dict = {}
        g: dict = {start: 0.0}

        while open_set:
            _, cur = heapq.heappop(open_set)
            if cur == end:
                path = []
                while cur in came_from:
                    path.append(cur)
                    cur = came_from[cur]
                path.append(start)
                return list(reversed(path))

            cx, cy = cur
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nb = (cx + dx, cy + dy)
                    if not self._walkable(*nb):
                        continue
                    ng = g[cur] + math.hypot(dx, dy)
                    if ng < g.get(nb, float("inf")):
                        came_from[nb] = cur
                        g[nb] = ng
                        heapq.heappush(open_set, (ng + h(nb, end), nb))
        return None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _walkable(self, x: int, y: int) -> bool:
        if x < 0 or y < 0 or x >= self._gw or y >= self._gh:
            return False
        return bool(self._grid[y, x])

    def _nearest_walkable(self, pt: tuple[int, int]) -> Optional[tuple[int, int]]:
        for r in range(1, 30):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r:
                        continue
                    nb = (pt[0] + dx, pt[1] + dy)
                    if self._walkable(*nb):
                        return nb
        return None

    def _to_grid(self, pt: tuple[float, float]) -> tuple[int, int]:
        return (int(pt[0] / GRID_SCALE), int(pt[1] / GRID_SCALE))

    def _to_pixel(self, gpt: tuple[int, int]) -> tuple[float, float]:
        half = GRID_SCALE / 2
        return (gpt[0] * GRID_SCALE + half, gpt[1] * GRID_SCALE + half)

    def _smooth(self, path: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Remove collinear / redundant waypoints using line-of-sight."""
        if len(path) <= 2:
            return path
        smoothed = [path[0]]
        i = 0
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1:
                if self._los(path[i], path[j]):
                    break
                j -= 1
            smoothed.append(path[j])
            i = j
        return smoothed

    def _los(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """Line-of-sight check between two pixel points."""
        ga, gb = self._to_grid(a), self._to_grid(b)
        steps  = max(abs(gb[0] - ga[0]), abs(gb[1] - ga[1]), 1)
        for i in range(steps + 1):
            t  = i / steps
            gx = int(ga[0] + t * (gb[0] - ga[0]))
            gy = int(ga[1] + t * (gb[1] - ga[1]))
            if not self._walkable(gx, gy):
                return False
        return True
