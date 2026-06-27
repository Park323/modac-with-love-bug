"""Aim-bot perception: detect on-screen players, tell enemy from friend, and
give an aim point — from a full game frame.

Pipeline (no custom training — pretrained detector + a color rule):

  1. detect   — pretrained YOLOv8 ``person`` boxes (game characters trigger the
                COCO person class well: ~0.85 conf in testing).
  2. viewmodel — drop the player's own hands/weapon (boxes that reach the bottom
                of the screen).
  3. IFF       — teammates carry a bright cyan outline; enemies have none. A cyan
                pixel fraction in/around the box separates them cleanly
                (~3.5% friend vs ~0% enemy).
  4. aim       — aim point = head, near the top-center of the box.

External callers use ``detect_n_pick_target(frame)`` — it detects, applies IFF,
and returns the single enemy to engage (or ``None``). ``detect_targets`` /
``pick_target`` are the underlying steps if you need all targets. Self-contained:
needs only ultralytics, torch, cv2, numpy. Mirrors :mod:`modac.radar`'s shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

# --- model -----------------------------------------------------------------
MODEL_PATH: str = "yolov8s.pt"   # ultralytics auto-downloads if absent
PERSON_CLASS: int = 0            # COCO 'person'
CONF_THRESH: float = 0.35

# --- own-viewmodel rejection -----------------------------------------------
# The player's hands/weapon render fixed at the bottom; real characters don't
# reach the screen bottom. Drop boxes whose bottom passes this fraction of H.
VIEWMODEL_Y_FRAC: float = 0.85

# --- IFF: teammates have a bright cyan outline, enemies have none -----------
CYAN_LO: tuple[int, int, int] = (82, 80, 120)    # HSV lower (teal/cyan glow)
CYAN_HI: tuple[int, int, int] = (100, 255, 255)  # HSV upper
CYAN_FRIEND_FRAC: float = 0.012  # cyan-pixel fraction above this = teammate
IFF_PAD: int = 8                 # px ring around the box to sample the outline

# --- aim point -------------------------------------------------------------
AIM_HEAD_FRAC: float = 0.12      # height within the box (0 = top edge) for the head


@dataclass(frozen=True)
class Target:
    box: tuple[int, int, int, int]  # x1, y1, x2, y2
    conf: float
    team: str                       # 'enemy' | 'friend'
    cyan: float                     # cyan fraction (IFF evidence)
    aim: tuple[int, int]            # aim point (x, y) — head


@lru_cache(maxsize=1)
def _model():
    from ultralytics import YOLO
    return YOLO(MODEL_PATH)


@lru_cache(maxsize=1)
def _device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _cyan_fraction(hsv: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """Fraction of cyan-outline pixels in/around a box (0..1)."""
    x1, y1, x2, y2 = box
    p = IFF_PAD
    crop = hsv[max(0, y1 - p):y2 + p, max(0, x1 - p):x2 + p]
    if crop.size == 0:
        return 0.0
    return float(cv2.inRange(crop, CYAN_LO, CYAN_HI).mean()) / 255.0


def detect_targets(frame: np.ndarray, *, conf: float = CONF_THRESH) -> list[Target]:
    """Detect players in a full game frame and classify enemy vs friend.

    Returns a list of :class:`Target`. Own-viewmodel boxes are dropped; the rest
    are tagged ``enemy``/``friend`` by the cyan-outline rule, each with a head
    aim point.
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    res = _model().predict(frame, classes=[PERSON_CLASS], conf=conf,
                           device=_device(), verbose=False)[0]
    targets: list[Target] = []
    for b in res.boxes:
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0])
        if y2 > VIEWMODEL_Y_FRAC * h:   # own hands / weapon
            continue
        cyan = _cyan_fraction(hsv, (x1, y1, x2, y2))
        team = "friend" if cyan > CYAN_FRIEND_FRAC else "enemy"
        aim = ((x1 + x2) // 2, int(y1 + AIM_HEAD_FRAC * (y2 - y1)))
        targets.append(Target((x1, y1, x2, y2), float(b.conf[0]), team, cyan, aim))
    return targets


def pick_target(targets: list[Target], frame_shape: tuple[int, int],
                *, by: str = "crosshair") -> Target | None:
    """Choose one enemy to engage. ``by='crosshair'`` picks the enemy whose aim
    point is nearest screen center; ``by='conf'`` picks the most confident."""
    enemies = [t for t in targets if t.team == "enemy"]
    if not enemies:
        return None
    if by == "conf":
        return max(enemies, key=lambda t: t.conf)
    h, w = frame_shape[:2]
    cx, cy = w / 2.0, h / 2.0
    return min(enemies, key=lambda t: (t.aim[0] - cx) ** 2 + (t.aim[1] - cy) ** 2)


def detect_n_pick_target(frame: np.ndarray, *, conf: float = CONF_THRESH,
                         by: str = "crosshair") -> Target | None:
    """**External entry point.** One call: detect players, tell enemy from friend,
    and return the single enemy to engage — or ``None`` if there's no enemy.

    ``Target.aim`` is the head's screen-pixel position in the input frame; the
    action subsystem aims/fires there. This module only reports *where*.
    """
    return pick_target(detect_targets(frame, conf=conf), frame.shape, by=by)


def _read(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"could not read frame: {path}")
    return img


def _annotate(frame: np.ndarray, targets: list[Target], chosen: Target | None) -> np.ndarray:
    vis = frame.copy()
    for t in targets:
        x1, y1, x2, y2 = t.box
        col = (255, 180, 0) if t.team == "friend" else (0, 0, 255)
        cv2.rectangle(vis, (x1, y1), (x2, y2), col, 3)
        cv2.putText(vis, f"{t.team.upper()} {t.conf:.2f} cyan={t.cyan*100:.1f}%",
                    (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        if t.team == "enemy":
            cv2.drawMarker(vis, t.aim, (0, 0, 255), cv2.MARKER_CROSS, 26, 3)
    if chosen is not None:  # highlight the engaged target
        cv2.circle(vis, chosen.aim, 16, (0, 255, 255), 3)
    return vis


if __name__ == "__main__":
    import sys
    from glob import glob

    args = sys.argv[1:] or sorted(glob("assets/test_cases/Crossfire20260628_*.bmp"))
    tiles = []
    for p in args:
        frame = _read(p)
        targets = detect_targets(frame)
        chosen = pick_target(targets, frame.shape)
        ne = sum(t.team == "enemy" for t in targets)
        nf = sum(t.team == "friend" for t in targets)
        print(f"{Path(p).name}: {ne} enemy, {nf} friend"
              + (f"  -> engage {chosen.aim}" if chosen else "  -> no enemy"))
        tiles.append(cv2.resize(_annotate(frame, targets, chosen), (0, 0), fx=0.6, fy=0.6))
    if tiles:
        w = max(t.shape[1] for t in tiles)
        tiles = [cv2.copyMakeBorder(t, 2, 2, 2, w - t.shape[1] + 2, cv2.BORDER_CONSTANT) for t in tiles]
        cv2.imwrite("_aim_montage.png", np.vstack(tiles))
        print("wrote _aim_montage.png")
