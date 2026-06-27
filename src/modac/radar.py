"""Real-capture radar front-end (high-res CrossFire captures, v2 assets).

Turns a full game frame into the two things the localizer needs:

  1. ``crop_radar``  — cut the radar disc (plus a margin so the N marker, which
     rides *outside* the ring, stays in frame at any yaw).
  2. ``detect_yaw``  — find the player's yaw by template-matching the ``north_marker``
     asset (the 'N' glyph + its ring tab) around the ring. The richer template is
     far more decisive than thresholding the bare 'N' blob.

Geometry is calibrated for 1600x900 captures with the radar ROI the user
measured at ``(28, 30)``-``(249, 252)``; base map ``assets/minimap_2.png``.

This module is self-contained: ``locate`` needs only cv2, numpy and the two
asset PNGs (``minimap_2.png`` + ``north_marker.png``) — no other project module —
so it can be dropped into an external server. Asset paths resolve relative to
this file's repo root, so the cwd doesn't matter. (The CLI's ``_verify_render``
is the only extra; it's also self-contained.)

``yaw`` convention (matches the rest of the project): degrees clockwise from
north, 0 = facing north.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

# Locate the assets dir robustly (works wherever this file is dropped): check
# the file's own dir, the cwd, and every ancestor for an ``assets/minimap_2.png``.
def _find_assets_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [here.parent / "assets", Path.cwd() / "assets"]
    candidates += [p / "assets" for p in here.parents]
    for c in candidates:
        if (c / "minimap_2.png").exists():
            return c
    return here.parent / "assets"


ASSETS_DIR: Path = _find_assets_dir()
NORTH_MARKER_PATH: str = str(ASSETS_DIR / "north_marker.png")
MAP_PATH: str = str(ASSETS_DIR / "minimap_2.png")

# --- capture geometry (1600x900 frames) ------------------------------------
ROI: tuple[int, int, int, int] = (28, 30, 249, 252)  # radar circle bbox (x0,y0,x1,y1)
MARGIN: int = 17                                      # room around the disc for the N marker

# Derived crop frame: the radar circle, measured in the *cropped* image.
_CX0, _CY0 = ROI[0] - MARGIN, ROI[1] - MARGIN
CIRCLE_CENTER: tuple[float, float] = ((ROI[0] + ROI[2]) / 2 - _CX0, (ROI[1] + ROI[3]) / 2 - _CY0)
CIRCLE_RADIUS: float = ((ROI[2] - ROI[0]) + (ROI[3] - ROI[1])) / 4  # ~110.8

# --- map placement, calibrated against the real radar ----------------------
BASE_OFFSET_DEG: float = 270.0   # minimap_2 portrait orientation -> north-up
ZOOM: float = 1.0                # base-px per radar-px (minimap_2 == radar scale)
VOID_BGR: tuple[int, int, int] = (60, 60, 60)  # off-map fill


def _rotation(deg: float) -> np.ndarray:
    a = np.radians(deg)
    ca, sa = np.cos(a), np.sin(a)
    return np.array([[ca, -sa], [sa, ca]], np.float64)


@lru_cache(maxsize=1)
def _map_bgr() -> np.ndarray:
    """``minimap_2`` as opaque BGR (alpha composited over the void fill)."""
    img = cv2.imread(MAP_PATH, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"could not read base map: {MAP_PATH}")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        bgr = img[:, :, :3].astype(np.float32)
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        return (bgr * a + np.array(VOID_BGR, np.float32) * (1.0 - a)).astype(np.uint8)
    return np.ascontiguousarray(img[:, :, :3])


def crop_radar(capture: np.ndarray) -> np.ndarray:
    """Cut the radar disc (with margin) out of a full game frame."""
    x0, y0, x1, y1 = ROI
    return capture[y0 - MARGIN:y1 + MARGIN, x0 - MARGIN:x1 + MARGIN]


@lru_cache(maxsize=2)
def _north_template(path: str) -> np.ndarray:
    """Grayscale north_marker, background masked out via its alpha."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"could not read north marker: {path}")
    gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    if img.shape[2] == 4:
        gray = cv2.bitwise_and(gray, gray, mask=(img[:, :, 3] > 10).astype(np.uint8) * 255)
    return gray


def detect_yaw(
    radar_crop: np.ndarray,
    *,
    marker_path: str = NORTH_MARKER_PATH,
    coarse_step: int = 6,
    fine_window: int = 6,
) -> tuple[float, float]:
    """Estimate yaw by finding the N marker around the ring.

    Rotates the crop so each candidate north angle is brought to top-center and
    template-matches the upright marker there; the most decisive angle wins. The
    rotation that lands N at the top is the player's yaw (the radar is player-up,
    so north sits at screen angle -yaw).

    Coarse-to-fine: scan every ``coarse_step`` degrees, then refine ±``fine_window``
    around the best at 1°. The marker's correlation-vs-angle peak is broad, so this
    matches the exhaustive 1° search (verified on all test cases) at ~5x less work.

    Returns ``(yaw, score)`` — yaw in degrees clockwise from north, score is the
    winning normalized-correlation (higher = more confident; a low score means
    the N was occluded / off-frame).
    """
    gray = cv2.cvtColor(radar_crop, cv2.COLOR_BGR2GRAY) if radar_crop.ndim == 3 else radar_crop
    tmpl = _north_template(marker_path)
    th, tw = tmpl.shape
    cx, cy = CIRCLE_CENTER
    half = tw // 2 + 40
    band_h = th + 24

    def score_at(deg: int) -> float:
        M = cv2.getRotationMatrix2D((cx, cy), deg, 1.0)
        rot = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]))
        band = rot[0:band_h, max(0, int(cx - half)):int(cx + half)]
        if band.shape[0] < th or band.shape[1] < tw:
            return -1.0
        return float(cv2.matchTemplate(band, tmpl, cv2.TM_CCOEFF_NORMED).max())

    best_score, best_deg = -1.0, 0
    for deg in range(0, 360, coarse_step):
        s = score_at(deg)
        if s > best_score:
            best_score, best_deg = s, deg
    for deg in range(best_deg - fine_window, best_deg + fine_window + 1):
        d = deg % 360
        s = score_at(d)
        if s > best_score:
            best_score, best_deg = s, d

    # deg = CCW rotation that brings N to top; yaw is clockwise from north.
    yaw = float((360 - best_deg) % 360)
    return yaw, best_score


def _read_capture(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"could not read capture: {path}")
    return img


def _affine() -> np.ndarray:
    """``A`` such that map_px = player + A @ (radar_px - center), at north-up."""
    return np.diag([ZOOM, ZOOM]).astype(np.float64) @ _rotation(BASE_OFFSET_DEG)


def _disc_mask(shape: tuple[int, int]) -> np.ndarray:
    cx, cy = CIRCLE_CENTER
    m = np.zeros(shape, np.uint8)
    cv2.circle(m, (int(cx), int(cy)), int(CIRCLE_RADIUS) - 6, 1, -1)
    cv2.circle(m, (int(cx), int(cy)), 24, 0, -1)  # drop the center chevron
    return m


VOID_GRAY: float = 60.0  # render void fill (matches render.VOID_BGR)
_MAP_PAD: int = 260      # void border so the disc can sit fully off-map


@lru_cache(maxsize=1)
def _big_map():
    """``minimap_2`` resampled into the north-up radar-pixel frame, void-padded.

    A point ``rp`` in a north-up radar maps to map pixel ``player + A@(rp-center)``
    with ``A = Z @ R(base_offset)``. So one warp of the whole map into this frame
    lets every candidate player position be evaluated as a plain template slide —
    no per-position re-render. Returns ``(BigP, BigP2, A, Op)`` where a template
    top-left ``loc`` recovers ``player = A @ (loc + center - Op)``.
    """
    mm = cv2.cvtColor(_map_bgr(), cv2.COLOR_BGR2GRAY)
    H, W = mm.shape
    A = _affine()
    Ainv = np.linalg.inv(A)
    corners = np.array([[0, 0], [W, 0], [0, H], [W, H]], np.float64)
    Q = (Ainv @ corners.T).T
    O = -Q.min(0)
    outw = int(np.ceil((Q.max(0) + O)[0])) + 2
    outh = int(np.ceil((Q.max(0) + O)[1])) + 2
    M = np.zeros((2, 3)); M[:, :2] = A; M[:, 2] = -(A @ O)
    big = cv2.warpAffine(mm.astype(np.float32), M.astype(np.float32), (outw, outh),
                         flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=VOID_GRAY)
    big = cv2.copyMakeBorder(big, _MAP_PAD, _MAP_PAD, _MAP_PAD, _MAP_PAD,
                             cv2.BORDER_CONSTANT, value=VOID_GRAY)
    Op = O + _MAP_PAD
    return big, big * big, A, Op


REAL_MAP_WH: tuple[int, int] = (1980, 654)  # real-map resolution (W, H)


def _to_real_map(x: int, y: int) -> tuple[int, int]:
    """minimap_2 pixel ``(x, y)`` → real-map coords.

    The real map is ``minimap_2.png`` rotated 90° clockwise (frame ``H×W`` =
    558×182) then scaled to ``REAL_MAP_WH``. Scale is **uniform**, fit to the
    shorter rotated axis (so that side maps exactly; the longer side slightly
    overshoots for now). CW90: ``(x, y) → (H-1-y, x)``.
    """
    h, w = _map_bgr().shape[:2]          # minimap_2 (H=558, W=182)
    rot_w, rot_h = h, w                   # after CW90: 558 wide, 182 tall
    xr, yr = h - 1 - y, x                 # rotated pixel coords
    # uniform scale from the shorter rotated side
    s = (REAL_MAP_WH[1] / rot_h) if rot_h <= rot_w else (REAL_MAP_WH[0] / rot_w)
    return int(round(xr * s)), int(round(yr * s))


def _locate_minimap2(capture: np.ndarray) -> tuple[int, int, float, float]:
    """Localize in *minimap_2* pixel coords (internal frame). See :func:`locate`
    for the public, real-map-oriented output.

    Orientation (base_offset) and scale (zoom) are calibrated, so only the
    translation is unknown: the north-up radar disc is matched against the whole
    pre-warped map (:func:`_big_map`) in one shot via masked normalized
    cross-correlation, evaluated at *every* pixel — including positions where the
    disc hangs off the map edge (half void). The masked NCC is built from three
    correlations so it stays exact while running in ~0.1s instead of a per-pixel
    re-render sweep.
    """
    big, big2, A, Op = _big_map()

    crop = crop_radar(capture)
    yaw, _ = detect_yaw(crop)
    deg = int(round((360 - yaw) % 360))  # rotate N to top → north-up
    nup = cv2.warpAffine(crop, cv2.getRotationMatrix2D(CIRCLE_CENTER, deg, 1.0),
                         (crop.shape[1], crop.shape[0]))
    realg = cv2.cvtColor(nup, cv2.COLOR_BGR2GRAY).astype(np.float32)
    w = _disc_mask(realg.shape).astype(np.float32)
    w[realg > 205] = 0  # drop view-cone glare
    n = float(w.sum())

    # masked NCC over each window: num / sqrt(var_window * var_template)
    tbar = float((w * realg).sum() / n)
    t0 = (w * (realg - tbar)).astype(np.float32)
    var_t = float((w * (realg - tbar) ** 2).sum())
    num = cv2.matchTemplate(big, t0, cv2.TM_CCORR)
    s_i = cv2.matchTemplate(big, w, cv2.TM_CCORR)
    s_ii = cv2.matchTemplate(big2, w, cv2.TM_CCORR)
    var_w = s_ii - s_i * s_i / n
    floor = 1e-3 * float(var_w.max())  # reject near-uniform (all-void) windows
    ncc = np.where(var_w > floor, num / np.sqrt(np.maximum(var_w, floor) * var_t), -1.0)

    _, score, _, loc = cv2.minMaxLoc(ncc)
    cx, cy = CIRCLE_CENTER
    player = A @ (np.array([loc[0] + cx, loc[1] + cy]) - Op)
    return int(round(player[0])), int(round(player[1])), yaw, float(score)


def locate(capture: np.ndarray) -> tuple[int, int, float, float]:
    """Full classical localization: capture → ``(x, y, yaw, score)``.

    ``x, y`` is the player in **real-map** coordinates (``minimap_2`` rotated 90°
    clockwise — the orientation the rest of the system uses). ``yaw`` is degrees
    clockwise from north (world heading, unaffected by the map's image rotation);
    ``score`` is the masked-NCC fit (higher = more confident; the map's repeated
    blocks make mid-map positions less decisive).
    """
    x, y, yaw, score = _locate_minimap2(capture)
    mx, my = _to_real_map(x, y)
    return mx, my, yaw, score


def _render_disc(x: int, y: int) -> np.ndarray:
    """Synthetic north-up radar map disc at player ``(x, y)`` (self-contained;
    mirrors localize_net.render.render_map's map layer at yaw=0)."""
    A = _affine()
    t = np.array([x, y], np.float64) - A @ np.array(CIRCLE_CENTER, np.float64)
    M = np.hstack([A, t[:, None]]).astype(np.float32)
    h, w = MARGIN * 2 + (ROI[3] - ROI[1]), MARGIN * 2 + (ROI[2] - ROI[0])
    return cv2.warpAffine(_map_bgr(), M, (w, h),
                          flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderValue=VOID_BGR)


def _verify_render(capture: np.ndarray, x: int, y: int, yaw: float) -> np.ndarray:
    """Side-by-side: real radar (de-rotated north-up) | synthetic map at (x,y).
    For eyeballing whether a localization is right."""
    crop = crop_radar(capture)
    deg = int(round((360 - yaw) % 360))
    nup = cv2.warpAffine(crop, cv2.getRotationMatrix2D(CIRCLE_CENTER, deg, 1.0),
                         (crop.shape[1], crop.shape[0]))
    disc = np.zeros(nup.shape[:2], np.uint8)
    cv2.circle(disc, (int(CIRCLE_CENTER[0]), int(CIRCLE_CENTER[1])), int(CIRCLE_RADIUS) - 2, 255, -1)
    real = nup.copy(); real[disc == 0] = (30, 30, 30)
    syn = _render_disc(x, y); syn[disc == 0] = (30, 30, 30)
    return np.hstack([real, syn])


if __name__ == "__main__":
    import sys
    import time
    from glob import glob

    start = time.perf_counter()
    args = sys.argv[1:] or sorted(glob("assets/test_cases/*.bmp"))
    tiles, pts = [], []
    for p in args:
        cap = _read_capture(p)
        # render/plot in minimap_2 frame (where the assets live); report real-map coords.
        x, y, yaw, score = _locate_minimap2(cap)
        mx, my = _to_real_map(x, y)
        print(f"{Path(p).name}: map=({mx},{my})  [minimap2=({x},{y})]  yaw={yaw:6.1f}  score={score:.3f}")
        pair = _verify_render(cap, x, y, yaw)
        cv2.putText(pair, f"{Path(p).stem[-4:]} map({mx},{my}) {score:.2f}", (4, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 0), 1)
        tiles.append(cv2.copyMakeBorder(pair, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(80, 80, 80)))
        pts.append((x, y, Path(p).stem[-2:]))

    if tiles:
        rows = [np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)]
        w = max(r.shape[1] for r in rows)
        rows = [cv2.copyMakeBorder(r, 0, 0, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(20, 20, 20)) for r in rows]
        cv2.imwrite("_loc_montage.png", np.vstack(rows))
        mm = cv2.cvtColor(cv2.imread(MAP_PATH, cv2.IMREAD_UNCHANGED)[:, :, :3], cv2.COLOR_BGR2GRAY)
        g = cv2.cvtColor(mm, cv2.COLOR_GRAY2BGR)
        for x, y, lbl in pts:
            cv2.circle(g, (x, y), 4, (0, 0, 255), -1)
            cv2.putText(g, lbl, (x + 4, y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)
        cv2.imwrite("_loc_on_map.png", cv2.resize(g, (0, 0), fx=1.7, fy=1.7, interpolation=cv2.INTER_NEAREST))
        print("wrote _loc_montage.png and _loc_on_map.png")

    print(f"done in {time.perf_counter() - start:.2f}s")
