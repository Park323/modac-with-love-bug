"""Minimap → world localization: estimate the player's map position and yaw
from a cropped minimap.

Pipeline (matches the original pseudo-algorithm):

  1. yaw   — rotate ``map_bound`` 1° at a time; the angle that brings the 'N'
             compass marker to top-center (12 o'clock) is the player's yaw.
  2. align — rotate ``map_bound`` by that yaw so north points up.
  3. place — slide the north-up minimap over ``full_map_alpha`` (multi-scale
             template match) to find where its center sits on the full map.

Returns ``(x, y, yaw)``: ``x, y`` are the minimap center in ``full_map_alpha``
pixel coordinates, ``yaw`` is degrees clockwise from north (0 = facing north).

Geometry constants below were calibrated once against ``sample_image.png`` with
``map_bound`` cropped at bbox (6, 6, 194, 194) — a 188×188 patch with a 16 px
margin around the radar circle so the 'N' marker (which sits ~5 px *outside*
the circle edge) stays in frame at any rotation. Re-calibrate if the HUD
layout or crop changes.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# minimap 높이 67 (48:115)
# --- map_bound (188×188) coordinate-system constants, calibrated on sample_image ---
CIRCLE_CENTER: tuple[float, float] = (101.0, 99.5)   # radar circle center (subpixel)
CIRCLE_RADIUS: int = 79                              # radar circle radius
NORTH_TARGET: tuple[int, int] = (94, 11)     # where the N marker lands when at 12 o'clock
# N marker isolation: it sits in a thin annulus just outside the circle edge and
# is pure white (>215), brighter than the grey circle rim — so a brightness
# threshold cleanly picks it out without any rotation search.
NORTH_RING: tuple[int, int] = (76, 92)        # radial band (px) the N marker lives in
NORTH_THRESH: int = 215                        # grayscale cutoff isolating the white N
NORTH_MIN_AREA: int = 4                         # ignore bright specks smaller than this (px)

# Zoom is fixed, so the minimap→full pixel scale is a constant (non-uniform:
# the minimap is squashed vertically vs the full map). Calibrated on sample_image.
ZOOM_SCALE: tuple[float, float] = (13.4, 9.7)   # (x, y) full-px per minimap-px

# --- patched_map reference -------------------------------------------------
# patched_map.png replaces full_map_outline_thick as the match target. It is the
# SAME level but (a) rotated 90° CW from north-up, (b) a textured grayscale
# drawing (alpha is uniform, content lives in BGR — so we edge-detect the BGR,
# not the alpha), and (c) ~4× smaller: rotated north-up it is 487×164, i.e.
# the old 1980×654 map ÷4.065 (x) and ÷3.988 (y).
PATCHED_ROT = cv2.ROTATE_90_CLOCKWISE            # bring patched_map north-up
# patched_map is a textured drawing: its block ink is dark (gray < ~100) while
# the gray shading bands sit at 120-180. Thresholding the ink drops the bands so
# they don't inject spurious horizontal edges into the match.
PATCHED_INK_THRESH: int = 100
# PROVISIONAL — minimap→patched scale (patched-px per minimap-content-px).
# The analytic ZOOM_SCALE÷4 guess (3.30, 2.43) is wrong: a scale sweep against
# this sample peaks near ~0.4 of the content-bbox crop, not 3.3×. Treat this as a
# placeholder until calibrated against a known (x, y) ground-truth — see notes in
# match_on_full_map. Keeps the old ~1.38 x/y squash ratio.
ZOOM_SCALE_PATCHED: tuple[float, float] = (0.46, 0.33)  # (x, y) patched-px per minimap-px


def _load_gray(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")
    return img


def _alignment_score(aligned_gray: np.ndarray) -> float:
    """How well the rotated minimap's block edges line up with the image axes.

    When the map is correctly north-up, its walls/blocks run horizontal &
    vertical, so Canny edges pile into a few rows and columns. Summing the
    squared per-row and per-column edge counts rewards that concentration.
    """
    e = cv2.Canny(aligned_gray, 50, 120)
    cx, cy = CIRCLE_CENTER
    mask = np.zeros_like(aligned_gray)
    cv2.circle(mask, (round(cx), round(cy)), CIRCLE_RADIUS - 4, 255, -1)
    e = cv2.bitwise_and(e, e, mask=mask)
    rows = e.sum(1).astype(np.float64)
    cols = e.sum(0).astype(np.float64)
    return float((rows ** 2).sum() + (cols ** 2).sum())


def estimate_yaw(map_bound_gray: np.ndarray) -> tuple[float, float]:
    """Find the yaw (degrees clockwise from north) from the 'N' compass marker.

    The N marker is a pure-white blob in the annulus just outside the circle.
    But other bright things (the view cone, map blocks that reach the edge when
    the player is near a map border) can also land in the annulus, so a single
    centroid is unreliable. Instead we split the bright pixels into clusters,
    treat each as an N candidate, and keep the one whose implied rotation makes
    the map best axis-align (highest ``_alignment_score``). This rejects bright
    intruders without any fine search.

    Returns ``(yaw, score)``. ``yaw`` is degrees clockwise from north
    (0 = facing north). ``score`` is the winning alignment score; the runner-up
    margin indicates how decisive the pick was.
    """
    cx, cy = CIRCLE_CENTER
    h, w = map_bound_gray.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.hypot(xx - cx, yy - cy)
    blob = (dist >= NORTH_RING[0]) & (dist <= NORTH_RING[1]) & (map_bound_gray > NORTH_THRESH)

    n, _, stats, cents = cv2.connectedComponentsWithStats((blob * 255).astype(np.uint8), 8)
    candidates = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= NORTH_MIN_AREA]
    if not candidates:
        return 0.0, 0.0

    best_yaw, best_score = 0.0, -1.0
    for i in candidates:
        mx, my = cents[i]
        # N's radial angle from 12 o'clock (clockwise +); player yaw is its negative.
        theta = np.degrees(np.arctan2(mx - cx, -(my - cy)))
        yaw = float((-theta) % 360.0)
        score = _alignment_score(_rotate_keep(map_bound_gray, yaw))
        if score > best_score:
            best_score, best_yaw = score, yaw
    return best_yaw, best_score


def _rotate_keep(img: np.ndarray, yaw: float) -> np.ndarray:
    """Rotate clockwise by ``yaw`` degrees about the radar center."""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D(CIRCLE_CENTER, -yaw, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC)


def _minimap_block_edges(aligned_bgr: np.ndarray) -> np.ndarray:
    """Block outlines of the north-up minimap, inside the circle and with the
    center player arrow masked out."""
    gray = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 120)
    cx, cy = CIRCLE_CENTER
    mask = np.zeros(gray.shape, np.uint8)
    cv2.circle(mask, (round(cx), round(cy)), CIRCLE_RADIUS - 6, 255, -1)
    cv2.circle(mask, (round(cx), round(cy)), 11, 0, -1)  # drop the player arrow at the center
    return cv2.bitwise_and(edges, edges, mask=mask)


def _map_band_bbox(edges: np.ndarray) -> tuple[int, int, int, int]:
    """Bounding box of the map content: the run of rows/cols dense with edges
    (drops sparse view-cone / circle-rim noise)."""
    rowp = (edges > 0).sum(1)
    colp = (edges > 0).sum(0)
    rows = np.where(rowp > rowp.max() * 0.20)[0] if rowp.max() else np.array([0])
    cols = np.where(colp > colp.max() * 0.20)[0] if colp.max() else np.array([0])
    return int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1


def _north_up_full(full_alpha: np.ndarray) -> np.ndarray:
    """Rotate patched_map north-up and edge-detect its BGR content.

    patched_map's alpha is uniform, so the drawn map lives in the BGR channels.
    We isolate the dark block ink (dropping the gray shading bands) and take the
    boundary of that mask as the block outlines.
    """
    full = cv2.rotate(full_alpha, PATCHED_ROT)
    gray = cv2.cvtColor(full, cv2.COLOR_BGRA2GRAY) if full.ndim == 3 else full
    ink = ((gray < PATCHED_INK_THRESH).astype(np.uint8)) * 255
    return cv2.Canny(ink, 50, 150)


def _unrotate_cw(rx: int, ry: int, orig_h: int) -> tuple[int, int]:
    """Map a point from the north-up (90° CW-rotated) frame back to the original
    patched_map pixel coordinates."""
    return ry, (orig_h - 1) - rx


def match_on_full_map(
    aligned_bgr: np.ndarray,
    full_alpha: np.ndarray,
) -> tuple[int, int, float, dict]:
    """Place the north-up minimap on patched_map and find the player position.

    The minimap's map-content edges are upscaled to patched_map's (north-up)
    pixel scale and slid over the full-map edges; the best-correlating offset is
    the player location. ``aligned_bgr`` is the north-up minimap crop (player at
    its center). Returns ``(x, y, score, debug)`` where ``x, y`` is the player in
    *original* patched_map pixel coordinates and ``debug`` carries intermediates
    for the overlay.
    """
    full_edges = _north_up_full(full_alpha)
    fh, fw = full_edges.shape

    # minimap edges, with the center player arrow punched out.
    aligned_gray = (
        cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY) if aligned_bgr.ndim == 3 else aligned_bgr
    )
    edges = cv2.Canny(aligned_gray, 50, 120)
    h, w = aligned_gray.shape
    pcx, pcy = w / 2.0, h / 2.0  # player sits at the crop center
    # keep only inside the radar circle (drop the rim arc), then punch the arrow.
    cmask = np.zeros((h, w), np.uint8)
    cv2.circle(cmask, (round(pcx), round(pcy)), min(h, w) // 2 - 8, 255, -1)
    cv2.circle(cmask, (round(pcx), round(pcy)), 12, 0, -1)
    edges = cv2.bitwise_and(edges, edges, mask=cmask)

    # crop to the map-content band (drops sparse circle-rim / view-cone noise),
    # then upscale to patched scale to build the search template.
    x0, y0, x1, y1 = _map_band_bbox(edges)
    crop = edges[y0:y1, x0:x1]
    sx, sy = ZOOM_SCALE_PATCHED
    tw, th = max(int(crop.shape[1] * sx), 1), max(int(crop.shape[0] * sy), 1)
    tmpl = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_NEAREST)
    tmpl = tmpl[:fh, :fw]  # guard against rounding past the full-map bounds

    res = cv2.matchTemplate(full_edges, tmpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)

    # player = minimap center, expressed in the north-up full frame, then mapped
    # back to original patched_map coordinates.
    rx = int(loc[0] + (pcx - x0) * sx)
    ry = int(loc[1] + (pcy - y0) * sy)
    px, py = _unrotate_cw(rx, ry, full_alpha.shape[0])

    debug = {
        "loc": loc,
        "tmpl_size": (tmpl.shape[1], tmpl.shape[0]),
        "bbox": (x0, y0, x1, y1),
        "player_rot": (rx, ry),  # player in the north-up frame (for overlay)
    }
    return px, py, float(score), debug


def _render_overlay(
    full_alpha: np.ndarray,
    aligned_bgr: np.ndarray,
    player: tuple[int, int],
    debug: dict,
    out_path: str | Path,
) -> None:
    """Composite the matched minimap region onto patched_map so the fit is
    visually checkable: full-map block outlines in red, the placed minimap
    edges in green, and the player position as a magenta marker. Rendered in the
    north-up frame (patched_map rotated 90° CW)."""
    full = cv2.rotate(full_alpha, PATCHED_ROT)
    full_gray = cv2.cvtColor(full, cv2.COLOR_BGRA2GRAY) if full.ndim == 3 else full
    canvas = cv2.cvtColor(full_gray, cv2.COLOR_GRAY2BGR)
    canvas[cv2.Canny(full_gray, 50, 150) > 0] = (0, 0, 255)  # full outlines: red

    aligned_gray = (
        cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY) if aligned_bgr.ndim == 3 else aligned_bgr
    )
    edges = cv2.Canny(aligned_gray, 50, 120)
    x0, y0, x1, y1 = debug["bbox"]
    tw, th = debug["tmpl_size"]
    crop = cv2.resize(edges[y0:y1, x0:x1], (tw, th), interpolation=cv2.INTER_NEAREST)
    lx, ly = debug["loc"]
    h, w = canvas.shape[:2]
    sub = canvas[ly:ly + th, lx:lx + tw]
    cm = crop[:sub.shape[0], :sub.shape[1]] > 0
    sub[cm] = (0, 200, 0)  # placed minimap edges: green

    px, py = debug["player_rot"]  # player in this (north-up) frame
    if 0 <= px < w and 0 <= py < h:
        cv2.drawMarker(canvas, (px, py), (255, 0, 255), cv2.MARKER_CROSS, 24, 3)
        cv2.circle(canvas, (px, py), 12, (255, 0, 255), 2)
    cv2.imwrite(str(out_path), canvas)

def _rotate_image_around_center(src, center_x, center_y, angle_degrees):
    # 원래 이미지의 캔버스 크기 (가로, 세로) 구하기
    # src.shape는 (세로, 가로, 채널) 순서이므로 뒤집어서 (width, height)로 지정
    h, w = src.shape[:2]
    
    # 2. 회전 중심 좌표 설정
    center = (center_x, center_y)
    
    # 3. 아핀 변환을 위한 회전 행렬(Matrix) 생성
    # cv2.getRotationMatrix2D(중심좌표, 회전각도(도), 스케일)
    # 각도가 양수이면 반시계 방향, 음수이면 시계 방향으로 회전합니다.
    rotation_matrix = cv2.getRotationMatrix2D(center, angle_degrees, scale=1.0)
    
    # 4. 아핀 변환 적용
    # 크기를 원래 이미지 크기인 (w, h)로 지정하면 
    # 중심축은 (100, 100)에 고정된 채 회전하고, 벗어나는 영역은 자동으로 잘려 나갑니다.
    rotated_image = cv2.warpAffine(src, rotation_matrix, (w, h), flags=cv2.INTER_LINEAR)
    
    return rotated_image

def localize(
    map_bound_path: str | Path,
    full_map_alpha_path: str | Path,
    *,
    overlay_path: str | Path | None = None,
) -> tuple[int, int, float]:
    """Estimate ``(x, y, yaw)`` of the player on the full map from a minimap crop.

    ``x, y``  : minimap center (player) in ``full_map_alpha`` pixel coordinates.
    ``yaw``   : degrees clockwise from north (0 = facing north).

    If ``overlay_path`` is given, writes a debug image there showing how the
    minimap maps onto the full map (full outlines red, placed minimap green,
    player magenta) so the mapping quality can be checked at a glance.
    """
    map_bound_bgr = cv2.imread(str(map_bound_path), cv2.IMREAD_COLOR)
    if map_bound_bgr is None:
        raise FileNotFoundError(f"could not read map_bound: {map_bound_path}")
    full_alpha = cv2.imread(str(full_map_alpha_path), cv2.IMREAD_UNCHANGED)
    if full_alpha is None:
        raise FileNotFoundError(f"could not read full_map_alpha: {full_map_alpha_path}")

    map_bound_gray = cv2.cvtColor(map_bound_bgr, cv2.COLOR_BGR2GRAY)

    # 1) yaw  2) align north-up  3) place on full map
    yaw, _ = estimate_yaw(map_bound_gray)
    rotated_screen = _rotate_image_around_center(
        map_bound_gray, *CIRCLE_CENTER, -yaw
    )
    aligned_mini = rotated_screen[
        int(CIRCLE_CENTER[0]-CIRCLE_RADIUS):int(CIRCLE_CENTER[0]+CIRCLE_RADIUS),
        int(CIRCLE_CENTER[1]-CIRCLE_RADIUS):int(CIRCLE_CENTER[1]+CIRCLE_RADIUS)
    ]
    
    x, y, _, debug = match_on_full_map(aligned_mini, full_alpha)

    if overlay_path is not None:
        _render_overlay(full_alpha, aligned_mini, (x, y), debug, overlay_path)
    return x, y, yaw


if __name__ == "__main__":
    import sys

    mb = sys.argv[1] if len(sys.argv) > 1 else "map_bound.png"
    fm = sys.argv[2] if len(sys.argv) > 2 else "patched_map.png"
    ov = sys.argv[3] if len(sys.argv) > 3 else None
    print(localize(mb, fm, overlay_path=ov))
