"""Synthesize HUD radar crops from a fixed base map + real HUD assets.

Inverse of :mod:`modac.localize`: given the player position ``(x, y)`` in
base-map pixels and ``yaw`` (degrees clockwise from north), composite the radar
the HUD would show. The base map and overlay assets are fixed, so every render
is a free, perfectly-labeled training sample.

Composite, in the ring asset's native frame:
  1. warp the base map into the radar disc:
         map_pt = player + Z @ R(base_offset + yaw_sign*yaw) @ (m - c)
     (``Z`` = zoom in base-px per radar-px, ``c`` = ring circle center, ``R`` an
      image-coords rotation; ``base_offset`` encodes which way north points).
  2. mask to the circle.
  3. overlay ``ring.png`` rotated about ``c`` by ``yaw`` — the baked-in N marker
     rides to screen angle -yaw, exactly as the real player-up radar does.
  4. overlay ``marker.png`` (the player chevron) at ``c``, always pointing up.

``yaw`` convention: 0 = facing north, increasing clockwise — identical to
``modac.localize.estimate_yaw``. For ``patched_map.png`` north points LEFT, so
``base_offset_deg = -90``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

VOID_BGR: tuple[int, int, int] = (60, 60, 60)

# Real in-game frames used as backgrounds behind the radar disc, so the net
# learns to ignore whatever surrounds the minimap. ROI crops out the HUD edges.
DEFAULT_BACKGROUNDS: tuple[str, ...] = ("sample_2.png", "sample_3.png", "sample_4.png", "sample_5.png")
DEFAULT_BG_ROI: tuple[int, int, int, int] = (200, 200, 1000, 1000)  # x0, y0, x1, y1


class BackgroundBank:
    """Random game-world background patches for the radar to sit on."""

    def __init__(self, paths=DEFAULT_BACKGROUNDS, roi=DEFAULT_BG_ROI):
        x0, y0, x1, y1 = roi
        self.crops: list[np.ndarray] = []
        for p in paths:
            im = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if im is None:
                continue
            self.crops.append(np.ascontiguousarray(
                im[y0:min(y1, im.shape[0]), x0:min(x1, im.shape[1])]))
        if not self.crops:
            raise FileNotFoundError(f"no backgrounds loaded from {paths}")

    def sample(self, w: int, h: int, rng: np.random.Generator) -> np.ndarray:
        c = self.crops[int(rng.integers(len(self.crops)))]
        ch, cw = c.shape[:2]
        if ch < h or cw < w:
            c = cv2.resize(c, (max(w, cw), max(h, ch)))
            ch, cw = c.shape[:2]
        x = int(rng.integers(0, cw - w + 1))
        y = int(rng.integers(0, ch - h + 1))
        return c[y:y + h, x:x + w].copy()


@dataclass(frozen=True)
class RadarSpec:
    """Geometry + asset paths to render a radar crop from a base map."""

    map_path: str = "patched_map.png"
    ring_path: str = "ring.png"
    marker_path: str = "marker.png"
    # Ring circle, measured from ring.png alpha (158x168 canvas).
    canvas_wh: tuple[int, int] = (158, 168)
    center: tuple[float, float] = (78.4, 87.3)
    radius: float = 78.8
    zoom: tuple[float, float] = (3.0, 3.0)   # (zx, zy) base-px per radar-px (uniform)
    base_offset_deg: float = -90.0           # patched_map: north = LEFT
    yaw_sign: float = 1.0
    ring_rot_sign: float = 1.0               # ring rotation direction (locked by round-trip)
    margin_frac: float = 0.04                # inset player from arena edge
    # FOV view-cone: semi-transparent white sector pointing up (player faces up).
    cone_deg: float = 90.0                   # full angular width
    cone_alpha: float = 0.22                 # peak opacity at the center
    cone_radius_frac: float = 0.95           # extent, as a fraction of the disc radius


PATCHED_SPEC = RadarSpec()


@dataclass
class WorldMap:
    bgr: np.ndarray  # (H, W, 3) uint8

    @property
    def height(self) -> int:
        return self.bgr.shape[0]

    @property
    def width(self) -> int:
        return self.bgr.shape[1]

    @classmethod
    def load(cls, path: str | Path) -> "WorldMap":
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"could not read base map: {path}")
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            bgr = img[:, :, :3].astype(np.float32)
            a = img[:, :, 3:4].astype(np.float32) / 255.0
            bgr = (bgr * a + np.array(VOID_BGR, np.float32) * (1.0 - a)).astype(np.uint8)
        else:
            bgr = img[:, :, :3]
        return cls(np.ascontiguousarray(bgr))

    @classmethod
    def for_spec(cls, spec: RadarSpec) -> "WorldMap":
        return cls.load(spec.map_path)


@lru_cache(maxsize=8)
def _load_rgba(path: str, w: int, h: int) -> np.ndarray:
    """Load an overlay asset as float RGBA (HxWx4, 0..1 alpha), fit to (w, h)."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"could not read asset: {path}")
    if img.shape[2] == 3:
        img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
    if (img.shape[1], img.shape[0]) != (w, h):
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    out = img.astype(np.float32)
    out[:, :, 3] /= 255.0
    return out


@lru_cache(maxsize=8)
def _marker_centered(marker_path: str, w: int, h: int, cx: float, cy: float) -> np.ndarray:
    """Player chevron placed on a (h, w) RGBA canvas with its centroid at (cx, cy)."""
    raw = cv2.imread(marker_path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise FileNotFoundError(marker_path)
    if raw.shape[2] == 3:
        raw = np.dstack([raw, np.full(raw.shape[:2], 255, np.uint8)])
    a = raw[:, :, 3]
    ys, xs = np.where(a > 10)
    sprite = raw[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    sh, sw = sprite.shape[:2]
    canvas = np.zeros((h, w, 4), np.float32)
    x0, y0 = int(round(cx - sw / 2)), int(round(cy - sh / 2))
    x0, y0 = max(0, min(w - sw, x0)), max(0, min(h - sh, y0))
    patch = sprite.astype(np.float32)
    patch[:, :, 3] /= 255.0
    canvas[y0:y0 + sh, x0:x0 + sw] = patch
    return canvas


@lru_cache(maxsize=8)
def _view_cone(center: tuple[float, float], radius: float, half_deg: float,
               peak_alpha: float, w: int, h: int) -> np.ndarray:
    """White FOV sector pointing up (player-up), faded radially. RGBA, 0..1 alpha.

    Yaw-independent (the player always faces up on the radar), so it's cached.
    """
    cx, cy = center
    yy, xx = np.mgrid[0:h, 0:w]
    dx, dy = xx - cx, yy - cy
    dist = np.hypot(dx, dy)
    ang = np.degrees(np.arctan2(dx, -dy))      # 0 = straight up, +/- to the sides
    in_sector = (np.abs(ang) <= half_deg) & (dist <= radius) & (dist > 2)
    falloff = np.clip(1.0 - dist / max(radius, 1.0), 0.0, 1.0)
    alpha = np.where(in_sector, peak_alpha * falloff, 0.0).astype(np.float32)
    rgba = np.zeros((h, w, 4), np.float32)
    rgba[:, :, :3] = 255.0
    rgba[:, :, 3] = alpha
    return rgba


def _alpha_over(bg_bgr: np.ndarray, fg_rgba: np.ndarray, gain: float = 1.0) -> np.ndarray:
    a = (fg_rgba[:, :, 3:4] * gain)
    return (bg_bgr.astype(np.float32) * (1 - a) + fg_rgba[:, :, :3] * a).astype(np.uint8)


def _rotation(deg: float) -> np.ndarray:
    a = np.radians(deg)
    ca, sa = np.cos(a), np.sin(a)
    return np.array([[ca, -sa], [sa, ca]], np.float32)


def _affine(spec: RadarSpec, x: float, y: float, yaw: float, scale_jitter: float) -> np.ndarray:
    zx, zy = spec.zoom
    Z = np.diag([zx * scale_jitter, zy * scale_jitter]).astype(np.float32)
    A = Z @ _rotation(spec.base_offset_deg + spec.yaw_sign * yaw)
    c = np.array(spec.center, np.float32)
    t = np.array([x, y], np.float32) - A @ c
    return np.hstack([A, t[:, None]]).astype(np.float32)


def render_map(
    world: WorldMap,
    x: float,
    y: float,
    yaw: float,
    *,
    spec: RadarSpec = PATCHED_SPEC,
    background: np.ndarray | None = None,
    disc_opacity: float = 1.0,
    scale_jitter: float = 1.0,
) -> np.ndarray:
    """Background + the (optionally semi-transparent) map disc — no HUD overlays.

    The map disc is blended over ``background`` at ``disc_opacity`` so the game
    world shows through, mirroring the translucent in-game minimap. Heavy
    augmentation is meant to be applied to *this* layer; the crisp ring/marker
    go on afterward via :func:`overlay_hud`.
    """
    w, h = spec.canvas_wh
    mapcrop = cv2.warpAffine(
        world.bgr, _affine(spec, x, y, yaw, scale_jitter), (w, h),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT, borderValue=VOID_BGR,
    )
    if background is not None:
        base = cv2.resize(background, (w, h)) if background.shape[:2] != (h, w) else background.copy()
    else:
        base = np.full((h, w, 3), VOID_BGR, np.uint8)

    mask = np.zeros((h, w), np.float32)
    cv2.circle(mask, (round(spec.center[0]), round(spec.center[1])), round(spec.radius) - 1, 1.0, -1)
    a = (mask * disc_opacity)[:, :, None]
    return (base.astype(np.float32) * (1 - a) + mapcrop.astype(np.float32) * a).astype(np.uint8)


def overlay_hud(
    img: np.ndarray,
    yaw: float,
    *,
    spec: RadarSpec = PATCHED_SPEC,
    draw_ring: bool = True,
    draw_marker: bool = True,
    draw_cone: bool = True,
    ring_gain: float = 1.0,
    cone_gain: float = 1.0,
) -> np.ndarray:
    """Composite the opaque ring (rotated so N rides to -yaw), the FOV view-cone,
    and the player marker.

    Applied *after* augmentation, so the HUD stays crisp / noise-free. Layer
    order: ring (border) -> view-cone -> marker (on top).
    """
    w, h = spec.canvas_wh
    crop = img
    if draw_ring:
        ring = _load_rgba(spec.ring_path, w, h)
        M = cv2.getRotationMatrix2D(spec.center, spec.ring_rot_sign * yaw, 1.0)
        ring_rot = cv2.warpAffine(ring, M, (w, h), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        crop = _alpha_over(crop, ring_rot, ring_gain)
    if draw_cone:
        cone = _view_cone(spec.center, spec.radius * spec.cone_radius_frac,
                          spec.cone_deg / 2.0, spec.cone_alpha, w, h)
        crop = _alpha_over(crop, cone, cone_gain)
    if draw_marker:
        marker = _marker_centered(spec.marker_path, w, h, *spec.center)
        crop = _alpha_over(crop, marker)
    return crop


def render(
    world: WorldMap,
    x: float,
    y: float,
    yaw: float,
    *,
    spec: RadarSpec = PATCHED_SPEC,
    background: np.ndarray | None = None,
    disc_opacity: float = 1.0,
    draw_ring: bool = True,
    draw_marker: bool = True,
    scale_jitter: float = 1.0,
) -> np.ndarray:
    """Convenience: full radar composite (map layer + HUD), no augmentation."""
    img = render_map(world, x, y, yaw, spec=spec, background=background,
                     disc_opacity=disc_opacity, scale_jitter=scale_jitter)
    return overlay_hud(img, yaw, spec=spec, draw_ring=draw_ring, draw_marker=draw_marker)


def valid_xy_bounds(world: WorldMap, spec: RadarSpec = PATCHED_SPEC) -> tuple[float, float, float, float]:
    mx = world.width * spec.margin_frac
    my = world.height * spec.margin_frac
    return (mx, world.width - mx, my, world.height - my)


def _demo(spec: RadarSpec, out: str, upscale: int = 3) -> None:
    world = WorldMap.for_spec(spec)
    cx, cy = world.width / 2, world.height / 2
    yaws = [0, 45, 90, 135, 180, 225, 270, 315]
    tiles = []
    for yw in yaws:
        t = render(world, cx, cy, yw, spec=spec)
        t = cv2.resize(t, (0, 0), fx=upscale, fy=upscale, interpolation=cv2.INTER_NEAREST)
        cv2.putText(t, f"{yw}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        tiles.append(cv2.copyMakeBorder(t, 2, 2, 2, 2, cv2.BORDER_CONSTANT))
    cv2.imwrite(out, np.hstack(tiles))
    print(f"wrote {out}: yaw sweep {yaws} (map={spec.map_path})")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="_render_demo.png")
    a = ap.parse_args()
    _demo(PATCHED_SPEC, a.out)
