#!/usr/bin/env python3
"""Extract deep-blue object outlines from full_map_alpha and save them as a
line-art reference used by the minimap localization matcher.

Each deep-blue object (blocks, crates, outer wall) becomes a single closed
contour drawn at the requested line thickness. Use a thicker line to make the
sliding-window comparison more tolerant of small misalignments.

Examples:
    python scripts/make_full_map_outline.py                       # default 2px
    python scripts/make_full_map_outline.py --thickness 20 \\
        --out full_map_outline_thick.png                          # 10x thicker
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np


def make_outline(
    alpha_path: str,
    out_path: str,
    thickness: int = 2,
    min_area: int = 200,
) -> int:
    """Write a white-background, black-outline image of the deep-blue objects.

    Returns the number of objects drawn.
    """
    fa = cv2.imread(alpha_path, cv2.IMREAD_UNCHANGED)
    if fa is None:
        raise FileNotFoundError(alpha_path)
    b, g, r = fa[:, :, 0].astype(int), fa[:, :, 1].astype(int), fa[:, :, 2].astype(int)
    a = fa[:, :, 3]
    mx = np.maximum(np.maximum(r, g), b)

    # deep-blue objects only (exclude the black outlines baked into the alpha map)
    deep_blue = ((b - r >= 12) & (mx < 190) & (a > 0)).astype(np.uint8) * 255
    deep_blue = cv2.morphologyEx(deep_blue, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(deep_blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) >= min_area]

    canvas = np.full((fa.shape[0], fa.shape[1], 3), 255, np.uint8)
    cv2.drawContours(canvas, contours, -1, (0, 0, 0), thickness)
    cv2.imwrite(out_path, canvas)
    return len(contours)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", default="full_map_alpha.png", help="input full_map_alpha (BGRA) path")
    ap.add_argument("--out", default="full_map_outline.png", help="output outline image path")
    ap.add_argument("--thickness", type=int, default=2, help="outline line thickness in px")
    ap.add_argument("--min-area", type=int, default=200, help="ignore objects smaller than this (px^2)")
    args = ap.parse_args()

    n = make_outline(args.alpha, args.out, args.thickness, args.min_area)
    print(f"saved {args.out}: {n} objects, thickness={args.thickness}")
