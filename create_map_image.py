import json
import sys
from pathlib import Path

import cv2
import numpy as np

DEFAULT_INPUT = "assets/mapinfo.json"
DEFAULT_OUTPUT = "assets/map_preview.png"
SCALE   = 0.5
PADDING = 40

# BGR colors
C_BG          = (255, 255, 255)
C_WALL        = (60,  55,  50)
C_MAP_FILL    = (220, 213, 200)
C_OBJECT_FILL = (120, 115, 105)
C_OBJECT_EDGE = (55,  50,  45)
C_TEXT_LIGHT  = (240, 240, 240)
C_TEXT_DARK   = (30,  30,  30)


def px(x: float, y: float) -> tuple[int, int]:
    return (int(x * SCALE + PADDING), int(y * SCALE + PADDING))


def main() -> None:
    input_path  = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT

    path = Path(input_path)
    if not path.exists():
        print(f"[ERROR] {input_path} not found")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        info = json.load(f)

    size     = info["size"]
    canvas_w = int(size["width"]  * SCALE + PADDING * 2)
    canvas_h = int(size["height"] * SCALE + PADDING * 2)
    img      = np.full((canvas_h, canvas_w, 3), C_BG, dtype=np.uint8)

    # ── walls (outer polygon + walkable hole) ─────────────────────────────────
    for wall in info.get("walls", []):
        outer = np.array([px(p[0], p[1]) for p in wall["polygon"]], dtype=np.int32)
        cv2.fillPoly(img, [outer], C_WALL)
        cv2.polylines(img, [outer], isClosed=True, color=C_WALL, thickness=2)
        for hole in wall.get("holes", []):
            inner = np.array([px(p[0], p[1]) for p in hole], dtype=np.int32)
            cv2.fillPoly(img, [inner], C_MAP_FILL)
            cv2.polylines(img, [inner], isClosed=True, color=(100, 95, 88), thickness=1)

    # ── obstacles ─────────────────────────────────────────────────────────────
    for obj in info.get("objects", []):
        poly = np.array([px(p[0], p[1]) for p in obj["polygon"]], dtype=np.int32)
        cv2.fillPoly(img, [poly], C_OBJECT_FILL)
        cv2.polylines(img, [poly], isClosed=True, color=C_OBJECT_EDGE, thickness=1)
        bbox = obj.get("bbox")
        if bbox:
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            lx, ly = px(cx, cy)
            label = obj["id"]
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.3, 1)
            cv2.putText(img, label, (lx - tw // 2, ly + th // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, C_TEXT_LIGHT, 1, cv2.LINE_AA)

    # ── north indicator ───────────────────────────────────────────────────────
    nx, ny = PADDING - 15, PADDING - 15
    cv2.arrowedLine(img, (nx, ny + 15), (nx, ny - 5), C_TEXT_DARK, 2, tipLength=0.5)
    cv2.putText(img, "N", (nx - 5, ny - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, C_TEXT_DARK, 2, cv2.LINE_AA)

    # ── title ─────────────────────────────────────────────────────────────────
    title = f"{info.get('image', 'map')}  (pixel coords, scale={SCALE})"
    cv2.putText(img, title, (PADDING, canvas_h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, C_TEXT_DARK, 1, cv2.LINE_AA)

    # ── save ──────────────────────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(output_path, img)
    print(f"Saved → {output_path}  ({canvas_w}×{canvas_h} px)")


if __name__ == "__main__":
    main()
