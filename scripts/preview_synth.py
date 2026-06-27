#!/usr/bin/env python3
"""Dump a grid of augmented synthetic radar samples with their (x, y, yaw)
labels burned in, so the training data can be eyeballed before training.

    python scripts/preview_synth.py --n 16 --out _synth_preview.png

Uses only cv2/numpy (no torch), so it runs in the base project env.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import dataclasses  # noqa: E402

from modac.localize_net.render import (  # noqa: E402
    PATCHED_SPEC,
    BackgroundBank,
    WorldMap,
    overlay_hud,
    render_map,
    valid_xy_bounds,
)


def augment(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Mirror of dataset.AugConfig defaults (kept torch-free for previewing)."""
    f = img.astype(np.float32)
    f = (f - 128.0) * (1.0 + rng.uniform(-0.20, 0.20)) + 128.0   # contrast
    f += 255.0 * rng.uniform(-0.25, 0.25)                        # brightness
    f += rng.normal(0.0, 12.0, f.shape)                          # gauss noise
    f = np.clip(f, 0, 255).astype(np.uint8)
    if rng.random() < 0.15:
        f = cv2.GaussianBlur(f, (3, 3), 0)
    if rng.random() < 0.3:
        ok, enc = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(35, 85))])
        if ok:
            f = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", default=PATCHED_SPEC.map_path)
    ap.add_argument("--zoom", type=float, default=PATCHED_SPEC.zoom[0])
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--upscale", type=int, default=2)
    ap.add_argument("--no-aug", action="store_true", help="render clean (no augmentation)")
    ap.add_argument("--out", default="_synth_preview.png")
    a = ap.parse_args()

    spec = dataclasses.replace(PATCHED_SPEC, map_path=a.map, zoom=(a.zoom, a.zoom))
    world = WorldMap.for_spec(spec)
    bank = BackgroundBank()
    w, h = spec.canvas_wh
    x0, x1, y0, y1 = valid_xy_bounds(world, spec)
    rng = np.random.default_rng(a.seed)

    tiles = []
    for _ in range(a.n):
        x, y, yaw = rng.uniform(x0, x1), rng.uniform(y0, y1), rng.uniform(0, 360)
        bg = None if a.no_aug else bank.sample(w, h, rng)
        disc_op = 0.85 if a.no_aug else float(rng.uniform(0.55, 0.92))
        img = render_map(world, x, y, yaw, spec=spec, background=bg, disc_opacity=disc_op,
                         scale_jitter=1.0 if a.no_aug else 1.0 + rng.uniform(-0.04, 0.04))
        if not a.no_aug:
            img = augment(img, rng)  # heavy aug on map+bg only
        img = overlay_hud(img, yaw, spec=spec,
                          draw_marker=rng.random() >= 0.05, draw_ring=rng.random() >= 0.05)
        if not a.no_aug:  # pan the whole radar a few px (crop misalignment)
            dx, dy = rng.uniform(-6, 6), rng.uniform(-6, 6)
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)
        if a.upscale != 1:
            img = cv2.resize(img, (0, 0), fx=a.upscale, fy=a.upscale, interpolation=cv2.INTER_NEAREST)
        cv2.putText(img, f"({x:.0f},{y:.0f}) {yaw:.0f}deg", (4, img.shape[0] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1, cv2.LINE_AA)
        tiles.append(cv2.copyMakeBorder(img, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(40, 40, 40)))

    cols = a.cols
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    w = max(r.shape[1] for r in rows)
    rows = [cv2.copyMakeBorder(r, 0, 0, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40)) for r in rows]
    grid = np.vstack(rows)
    cv2.imwrite(a.out, grid)
    print(f"wrote {a.out}  ({a.n} samples, {grid.shape[1]}x{grid.shape[0]})")


if __name__ == "__main__":
    main()
