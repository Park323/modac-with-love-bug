"""On-the-fly synthetic dataset for minimap localization.

Each ``__getitem__`` picks a random ``(x, y, yaw)`` over the valid map region,
composites the radar (:mod:`.render`, using the real ring/marker assets), applies
domain-randomization augmentation, and returns a square ``out_size`` float tensor
plus normalized regression targets.

Targets are normalized to keep the loss well-scaled:
  x -> x / W,  y -> y / H        (both in [0, 1], in base-map pixels)
  yaw -> (sin yaw, cos yaw)       (unit circle, wrap-around free)

Per-index seeding makes a given ``(seed, index)`` reproducible — handy for a
fixed validation set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

from .render import (
    DEFAULT_BACKGROUNDS,
    PATCHED_SPEC,
    BackgroundBank,
    RadarSpec,
    WorldMap,
    overlay_hud,
    render_map,
    valid_xy_bounds,
)

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


@dataclass
class AugConfig:
    """Domain-randomization knobs. Disable individually for ablations."""

    out_size: int = 200
    brightness: float = 0.25        # +/- fractional brightness jitter
    contrast: float = 0.20          # +/- fractional contrast jitter
    gauss_noise: float = 12.0       # std of additive noise (0-255 scale)
    blur_prob: float = 0.15         # chance of a light gaussian blur (halved)
    scale_jitter: float = 0.04      # +/- fractional zoom error (sim2real slack)
    pan_px: float = 6.0             # +/- px translation of the whole radar (crop misalign)
    drop_marker_prob: float = 0.05  # rarely hide the player marker
    drop_ring_prob: float = 0.05    # rarely the ring/N is occluded
    disc_opacity: tuple[float, float] = (0.55, 0.92)  # map translucency (bg shows through)
    bg_prob: float = 0.9            # chance of a real game-world background behind the disc
    cone_gain: tuple[float, float] = (0.7, 1.2)  # FOV view-cone opacity jitter
    jpeg_prob: float = 0.3          # capture / compression artifacts
    jpeg_quality: tuple[int, int] = (35, 85)
    hud_noise: float = 4.0          # light noise on the (crisp) ring/marker layer


@dataclass
class SynthConfig:
    spec: RadarSpec = field(default_factory=lambda: PATCHED_SPEC)
    backgrounds: tuple[str, ...] = DEFAULT_BACKGROUNDS
    length: int = 20000             # samples per epoch (synthetic, so arbitrary)
    seed: int = 0
    aug: AugConfig = field(default_factory=AugConfig)


def _augment(img: np.ndarray, rng: np.random.Generator, aug: AugConfig) -> np.ndarray:
    f = img.astype(np.float32)
    if aug.contrast:
        f = (f - 128.0) * (1.0 + rng.uniform(-aug.contrast, aug.contrast)) + 128.0
    if aug.brightness:
        f += 255.0 * rng.uniform(-aug.brightness, aug.brightness)
    if aug.gauss_noise:
        f += rng.normal(0.0, aug.gauss_noise, f.shape)
    f = np.clip(f, 0, 255).astype(np.uint8)
    if aug.blur_prob and rng.random() < aug.blur_prob:
        f = cv2.GaussianBlur(f, (3, 3), 0)  # mild only (dropped the 5px kernel)
    if aug.jpeg_prob and rng.random() < aug.jpeg_prob:
        q = int(rng.integers(aug.jpeg_quality[0], aug.jpeg_quality[1] + 1))
        ok, enc = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, q])
        if ok:
            f = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return f


class MinimapSynthDataset(Dataset):
    """Infinite synthetic radar -> (x, y, yaw) dataset."""

    def __init__(self, cfg: SynthConfig, *, train: bool = True):
        self.cfg = cfg
        self.train = train
        self.world = WorldMap.for_spec(cfg.spec)
        self.bounds = valid_xy_bounds(self.world, cfg.spec)
        self.W, self.H = self.world.width, self.world.height
        self.bg = BackgroundBank(cfg.backgrounds) if cfg.backgrounds else None

    def __len__(self) -> int:
        return self.cfg.length

    def sample_pose(self, rng: np.random.Generator) -> tuple[float, float, float]:
        x0, x1, y0, y1 = self.bounds
        return float(rng.uniform(x0, x1)), float(rng.uniform(y0, y1)), float(rng.uniform(0.0, 360.0))

    def render_pose(self, x: float, y: float, yaw: float, rng: np.random.Generator) -> np.ndarray:
        aug = self.cfg.aug
        w, h = self.cfg.spec.canvas_wh

        # 1) background + (semi-transparent) map disc
        bg = self.bg.sample(w, h, rng) if (self.bg and rng.random() < aug.bg_prob) else None
        if self.train:
            sj = 1.0 + rng.uniform(-aug.scale_jitter, aug.scale_jitter) if aug.scale_jitter else 1.0
            disc_op = float(rng.uniform(*aug.disc_opacity))
        else:
            sj, disc_op = 1.0, sum(aug.disc_opacity) / 2
        img = render_map(self.world, x, y, yaw, spec=self.cfg.spec,
                         background=bg, disc_opacity=disc_op, scale_jitter=sj)

        # 2) heavy augmentation on the map+background layer only
        if self.train:
            img = _augment(img, rng, aug)

        # 3) crisp, opaque HUD on top (kept noise-free apart from a light dusting)
        draw_marker = draw_ring = True
        if self.train:
            draw_marker = rng.random() >= aug.drop_marker_prob
            draw_ring = rng.random() >= aug.drop_ring_prob
        cone_gain = float(rng.uniform(*aug.cone_gain)) if self.train else 1.0
        img = overlay_hud(img, yaw, spec=self.cfg.spec, draw_ring=draw_ring,
                          draw_marker=draw_marker, cone_gain=cone_gain)
        if self.train and aug.hud_noise:
            img = np.clip(img.astype(np.float32) + rng.normal(0, aug.hud_noise, img.shape), 0, 255).astype(np.uint8)

        # 4) pan the whole composite a few px so the net tolerates crop misalignment
        #    (label is unchanged: the same player position, just off-center in frame)
        if self.train and aug.pan_px:
            dx, dy = rng.uniform(-aug.pan_px, aug.pan_px), rng.uniform(-aug.pan_px, aug.pan_px)
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)

        if img.shape[:2] != (aug.out_size, aug.out_size):
            img = cv2.resize(img, (aug.out_size, aug.out_size), interpolation=cv2.INTER_AREA)
        return img

    def _to_tensor(self, img_bgr: np.ndarray) -> torch.Tensor:
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - _IMAGENET_MEAN) / _IMAGENET_STD
        return torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))

    def target(self, x: float, y: float, yaw: float) -> torch.Tensor:
        r = np.radians(yaw)
        return torch.tensor([x / self.W, y / self.H, np.sin(r), np.cos(r)], dtype=torch.float32)

    def __getitem__(self, idx: int):
        rng = np.random.default_rng((self.cfg.seed * 1_000_003 + idx) & 0xFFFFFFFF)
        x, y, yaw = self.sample_pose(rng)
        img = self.render_pose(x, y, yaw, rng)
        return self._to_tensor(img), self.target(x, y, yaw)


def denormalize_target(t: np.ndarray, W: int, H: int) -> tuple[float, float, float]:
    """(x/W, y/H, sin, cos) -> (x_px, y_px, yaw_deg)."""
    x, y = float(t[0]) * W, float(t[1]) * H
    yaw = float(np.degrees(np.arctan2(t[2], t[3]))) % 360.0
    return x, y, yaw
