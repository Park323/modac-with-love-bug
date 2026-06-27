"""
Vision-based scenario → waypoint planner.

Given a natural-language scenario (e.g. "GR로 시작해서 중앙을 가로질러 오른쪽 끝
적 스폰까지 침투"), this shows ``minimap_aligned.png`` to Claude and gets back an
ordered list of waypoints — the route points and final destination the bot must
pass through — as pixel coordinates *on the aligned minimap*. The model decides
how many waypoints the scenario needs.

Each returned waypoint carries:
  - ``x_aligned, y_aligned`` : pixel coords on minimap_aligned.png (558×182)
  - ``x_map, y_map``         : real-map coords (1980×654) — the frame the
                               navigator / locate() / pathfinder all use
  - ``rot``                  : facing in degrees clockwise from north, derived
                               from the path direction (not asked of the model,
                               which is unreliable at it)
  - ``label``                : short description of the point

Provider: Google Gemini via OpenRouter (OpenAI-compatible, vision).
Requires the OPENROUTER_API_KEY env var.

Usage (CLI):
  export OPENROUTER_API_KEY=sk-or-...
  python record_replay/src/scenario_to_waypoints.py "GR로 시작해 중앙 통제실을
      지나 오른쪽 끝 적 스폰까지 침투" --save plan.json --viz plan_viz.png
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL       = "google/gemini-2.5-pro"

# ── geometry (matches radar.py) ────────────────────────────────────────────────

# minimap_aligned.png is minimap_2.png rotated 90° clockwise: 558 wide × 182 tall.
# real-map (the navigator/locate frame) is the same orientation scaled uniformly
# by the shorter axis, exactly as radar._to_real_map does:  real = aligned * S.
REAL_MAP_WH = (1980, 654)          # (W, H)
ALIGNED_WH  = (558, 182)           # (W, H)
SCALE       = REAL_MAP_WH[1] / ALIGNED_WH[1]   # 654 / 182 ≈ 3.5934

# Spawns, in real-map coords (from auto_run_optimized.py), and their aligned-px
# equivalents — handed to the model as anchors so its coordinate sense is calibrated.
GR_SPAWN_MAP = (1901.0, 123.0)
BL_SPAWN_MAP = (116.0, 261.0)


def _map_to_aligned(x: float, y: float) -> tuple[int, int]:
    return round(x / SCALE), round(y / SCALE)


def _aligned_to_map(x: float, y: float) -> tuple[int, int]:
    return round(x * SCALE), round(y * SCALE)


GR_SPAWN_ALIGNED = _map_to_aligned(*GR_SPAWN_MAP)   # ≈ (529, 34)
BL_SPAWN_ALIGNED = _map_to_aligned(*BL_SPAWN_MAP)   # ≈ (32, 73)


def _find_asset(name: str) -> Path | None:
    """Locate an asset file wherever this module is dropped (mirrors radar.py)."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "assets",                  # record_replay/assets
        Path.cwd() / "assets",
        Path.cwd() / "record_replay" / "assets",
        Path.cwd() / "ui" / "playtest",
    ]
    candidates += [p / "assets" for p in here.parents]
    candidates += [p / "ui" / "playtest" for p in here.parents]
    for c in candidates:
        if (c / name).exists():
            return c / name
    return None


def _find_minimap() -> Path:
    p = _find_asset("minimap_aligned.png")
    if p is None:
        raise FileNotFoundError("minimap_aligned.png not found in any assets dir")
    return p


# ── walkability snap (self-contained — no pathfinder import) ─────────────────────
# Mirrors pathfinder.MapPathfinder: a point inside (or too close to) an obstacle is
# pulled to the nearest walkable cell. Operates in real-map coords (1980×654).

GRID_SCALE = 4     # 1 grid cell = 4 px
OBS_MARGIN = 12    # px dilated around each obstacle (≈ character half-width)


def _build_walkable_grid(info: dict):
    """Bool grid [gh, gw], True = walkable. Walkable = inside the wall hole, minus
    obstacle polygons dilated by OBS_MARGIN."""
    import cv2
    import numpy as np

    W, H = info["size"]["width"], info["size"]["height"]
    full = np.zeros((H, W), dtype=np.uint8)
    for wall in info.get("walls", []):
        for hole in wall.get("holes", []):
            cv2.fillPoly(full, [np.array(hole, dtype=np.int32)], 255)

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (OBS_MARGIN * 2 + 1, OBS_MARGIN * 2 + 1))
    for obj in info.get("objects", []):
        mask = np.zeros((H, W), dtype=np.uint8)
        cv2.fillPoly(mask, [np.array(obj["polygon"], dtype=np.int32)], 255)
        full[cv2.dilate(mask, kernel) > 0] = 0

    gw, gh = W // GRID_SCALE, H // GRID_SCALE
    small = cv2.resize(full, (gw, gh), interpolation=cv2.INTER_NEAREST)
    return small > 127


def _snap_to_walkable(x_map: float, y_map: float, grid) -> tuple[int, int]:
    """Nearest walkable real-map coord to (x_map, y_map). Returns input unchanged
    if already walkable or no walkable cell is found within the search radius."""
    gh, gw = grid.shape
    gx, gy = int(x_map / GRID_SCALE), int(y_map / GRID_SCALE)

    def walkable(x, y):
        return 0 <= x < gw and 0 <= y < gh and bool(grid[y, x])

    if not walkable(gx, gy):
        found = None
        for r in range(1, 60):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r:
                        continue
                    if walkable(gx + dx, gy + dy):
                        found = (gx + dx, gy + dy)
                        break
                if found:
                    break
            if found:
                break
        if found is None:
            return round(x_map), round(y_map)
        gx, gy = found
    half = GRID_SCALE / 2
    return round(gx * GRID_SCALE + half), round(gy * GRID_SCALE + half)


# ── data ────────────────────────────────────────────────────────────────────────

@dataclass
class PlannedWaypoint:
    idx:       int
    label:     str
    x_aligned: float
    y_aligned: float
    x:         float    # real-map coord (1980×654) — what navigator/locate use
    y:         float

    def to_output(self) -> dict:
        """The required output shape: {idx, x, y}."""
        return {"idx": self.idx, "x": self.x, "y": self.y}


# ── prompt ────────────────────────────────────────────────────────────────────

_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "waypoints": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "x":     {"type": "number"},
                    "y":     {"type": "number"},
                },
                "required": ["label", "x", "y"],
            },
        },
    },
    "required": ["waypoints"],
}


def _system_prompt() -> str:
    w, h = ALIGNED_WH
    return (
        "You are a tactical route planner for the CrossFire map 'Transport Ship 2.0'. "
        "You are given a top-down minimap image and a natural-language scenario describing "
        "where a bot must go. Return the ordered list of waypoints — the route points the "
        "bot passes through plus the final destination — that accomplish the scenario.\n\n"
        f"COORDINATE SYSTEM: the image is {w} pixels wide and {h} pixels tall. The origin "
        "(0,0) is the TOP-LEFT corner; x increases to the RIGHT, y increases DOWNWARD. "
        "Report every waypoint as (x, y) pixel coordinates in this image, with x in "
        f"[0,{w}] and y in [0,{h}].\n\n"
        "KNOWN LANDMARKS (use these to calibrate your sense of scale and position):\n"
        f"  - GR team spawn  ≈ ({GR_SPAWN_ALIGNED[0]}, {GR_SPAWN_ALIGNED[1]})  (far RIGHT side)\n"
        f"  - BL team spawn  ≈ ({BL_SPAWN_ALIGNED[0]}, {BL_SPAWN_ALIGNED[1]})  (far LEFT side)\n\n"
        "IMPASSABLE AREAS — READ CAREFULLY:\n"
        "  - Any region enclosed by an outline (the rectangles, boxes, and polygons drawn "
        "    with dark lines) is a SOLID OBSTACLE. The bot CANNOT walk through, into, or "
        "    onto the inside of these shapes. Treat the line as a wall and everything it "
        "    encloses as filled-in solid.\n"
        "  - NEVER place a waypoint inside, on the edge of, or touching one of these "
        "    line-bounded shapes. Every waypoint must sit on the OPEN FLOOR — the empty "
        "    space BETWEEN the shapes — with clear margin from any outline.\n"
        "  - The dark border around the whole map is also impassable; stay well inside it.\n"
        "  - A straight line between two consecutive waypoints should not cut through any "
        "    enclosed shape; if it would, add an intermediate waypoint in the open floor to "
        "    route around the obstacle.\n\n"
        "RULES:\n"
        "  - Order waypoints in the sequence the bot should visit them; the last one is the "
        "    final destination.\n"
        "  - Choose as many waypoints as the route genuinely needs to round obstacles and "
        "    follow the described path — no more, no fewer.\n"
        "  - Give each a short label (e.g. 'start', 'center corridor', 'enemy spawn').\n"
    )


def _coerce_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines and lines[-1].startswith("```") else lines[1:])
    return json.loads(text)


# ── public API ────────────────────────────────────────────────────────────────

def plan_waypoints(
    scenario:     str,
    *,
    model:        str = DEFAULT_MODEL,
    minimap_path: str | Path | None = None,
    max_tokens:   int = 8192,
    snap:         bool = True,
) -> list[PlannedWaypoint]:
    """Plan a route for ``scenario`` by showing the aligned minimap to Gemini
    (via OpenRouter's OpenAI-compatible API).

    Returns waypoints in visit order, each with aligned-px coords, real-map coords,
    and a derived facing. The model decides how many waypoints the route needs.

    If ``snap`` and mapinfo.json is found, every waypoint is pulled to the nearest
    walkable cell so points the model dropped inside an obstacle are corrected.
    """
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY env var is not set")

    img_path = Path(minimap_path) if minimap_path else _find_minimap()
    img_b64 = base64.standard_b64encode(img_path.read_bytes()).decode("ascii")
    data_url = f"data:image/png;base64,{img_b64}"

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "route", "strict": True, "schema": _RESULT_SCHEMA},
        },
        messages=[
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Scenario:\n{scenario}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    )

    raw = response.choices[0].message.content
    if not raw:
        raise RuntimeError(f"Empty response from model (finish_reason="
                           f"{response.choices[0].finish_reason})")
    parsed = _coerce_json(raw)

    # clamp to image bounds, convert to real-map, then derive facings from the path
    w, h = ALIGNED_WH
    aligned = [
        (max(0.0, min(float(wp["x"]), w)), max(0.0, min(float(wp["y"]), h)), str(wp["label"]))
        for wp in parsed["waypoints"]
    ]
    pts_map = [(ax * SCALE, ay * SCALE) for ax, ay, _ in aligned]   # real-map floats

    # snap any point inside/near an obstacle to the nearest walkable cell
    if snap:
        info_path = _find_asset("mapinfo.json")
        if info_path is not None:
            grid = _build_walkable_grid(json.loads(info_path.read_text(encoding="utf-8")))
            pts_map = [_snap_to_walkable(x, y, grid) for x, y in pts_map]

    labels = [lbl for _, _, lbl in aligned]
    return [
        PlannedWaypoint(
            idx=i,
            label=labels[i],
            x_aligned=round(pts_map[i][0] / SCALE, 1),
            y_aligned=round(pts_map[i][1] / SCALE, 1),
            x=round(float(pts_map[i][0]), 1),
            y=round(float(pts_map[i][1]), 1),
        )
        for i in range(len(pts_map))
    ]


def to_output(waypoints: list[PlannedWaypoint]) -> list[dict]:
    """The required output: a flat ordered array of {idx, x, y} in real-map coords."""
    return [wp.to_output() for wp in waypoints]


def scenario_to_waypoints(scenario: str) -> list[dict]:
    """Convert a natural-language scenario string into [{idx, x, y}, ...]."""
    print(f"[scenario_to_waypoints] input: {scenario!r}", flush=True)
    output = to_output(plan_waypoints(scenario))
    print(f"[scenario_to_waypoints] output: {output}", flush=True)
    return output


def save_plan(waypoints: list[PlannedWaypoint], path: str | Path) -> None:
    """Write the required output array — [{idx, x, y}, ...] — as JSON."""
    Path(path).write_text(
        json.dumps(to_output(waypoints), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def visualize(waypoints: list[PlannedWaypoint], out_path: str | Path,
              minimap_path: str | Path | None = None) -> None:
    """Draw the planned route on the aligned minimap for eyeballing accuracy."""
    import cv2
    import numpy as np

    img_path = Path(minimap_path) if minimap_path else _find_minimap()
    img = cv2.imread(str(img_path), cv2.IMREAD_UNCHANGED)
    if img.ndim == 3 and img.shape[2] == 4:
        bgr = img[:, :, :3].astype(np.float32)
        a = img[:, :, 3:4].astype(np.float32) / 255.0
        img = (bgr * a + 245 * (1.0 - a)).astype(np.uint8)
    img = cv2.resize(img, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_NEAREST)

    pts = [(int(wp.x_aligned * 2), int(wp.y_aligned * 2)) for wp in waypoints]
    for a, b in zip(pts, pts[1:]):
        cv2.line(img, a, b, (0, 140, 255), 2)
    for i, (p, wp) in enumerate(zip(pts, waypoints)):
        cv2.circle(img, p, 6, (0, 0, 255), -1)
        cv2.putText(img, f"{i+1}", (p[0] + 6, p[1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(out_path), img)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Plan bot waypoints from a natural-language scenario.")
    ap.add_argument("scenario", help="natural-language scenario describing where the bot must go")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="OpenRouter model id (e.g. google/gemini-2.5-pro, google/gemini-2.5-flash)")
    ap.add_argument("--minimap", default=None, help="path to minimap_aligned.png")
    ap.add_argument("--save", default=None, help="write the plan JSON here")
    ap.add_argument("--viz", default=None, help="write a route-overlay PNG here")
    ap.add_argument("--no-snap", action="store_true",
                    help="skip pulling waypoints onto walkable cells (raw model coords)")
    args = ap.parse_args()

    wps = plan_waypoints(args.scenario, model=args.model,
                         minimap_path=args.minimap, snap=not args.no_snap)

    # the required output: [{idx, x, y}, ...] to stdout
    print(json.dumps(to_output(wps), ensure_ascii=False, indent=2))

    if args.save:
        save_plan(wps, args.save)
    if args.viz:
        visualize(wps, args.viz, minimap_path=args.minimap)


if __name__ == "__main__":
    main()
