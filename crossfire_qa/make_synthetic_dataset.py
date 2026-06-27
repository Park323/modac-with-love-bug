from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


ROI_1920X1080: dict[str, tuple[int, int, int, int]] = {
    "top_score_bar": (780, 0, 390, 68),
    "kill_feed_area": (1320, 53, 585, 135),
    "minimap": (31, 42, 255, 255),
    "hp_ac_area": (22, 980, 360, 75),
    "weapon_ammo_area": (1480, 917, 370, 120),
    "crosshair": (900, 480, 120, 120),
}


@dataclass
class SyntheticCase:
    name: str
    expected_behavior: str
    description: str


CASES: dict[str, SyntheticCase] = {
    "control_pass": SyntheticCase(
        name="control_pass",
        expected_behavior="PASS",
        description="Original video copied without mutation.",
    ),
    "score_hidden_fail": SyntheticCase(
        name="score_hidden_fail",
        expected_behavior="FAIL kill_count_increment",
        description="Top score bar is hidden, so kill feed may be detected but score increments cannot be verified.",
    ),
    "kill_feed_hidden_observe": SyntheticCase(
        name="kill_feed_hidden_observe",
        expected_behavior="PASS or fewer kill checks",
        description="Kill feed area is hidden. Under the current policy, score-only candidates should not fail notification QA.",
    ),
    "hud_hidden_respawn_fail": SyntheticCase(
        name="hud_hidden_respawn_fail",
        expected_behavior="FAIL or UNCERTAIN respawn_operation_after_death",
        description="HUD return cues are hidden, making respawn/playable-state validation fail or become uncertain.",
    ),
}


def discover_videos(dataset: Path) -> list[Path]:
    if dataset.is_file():
        return [dataset.resolve()]
    return sorted(p.resolve() for p in dataset.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def clip_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = box
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    x2 = max(x + 1, min(x + w, width))
    y2 = max(y + 1, min(y + h, height))
    return x, y, x2 - x, y2 - y


def scaled_box(name: str, width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = ROI_1920X1080[name]
    sx = width / 1920.0
    sy = height / 1080.0
    return clip_box((round(x * sx), round(y * sy), round(w * sx), round(h * sy)), width, height)


def fill_roi(frame: np.ndarray, name: str, color: tuple[int, int, int] = (0, 0, 0)) -> None:
    height, width = frame.shape[:2]
    x, y, w, h = scaled_box(name, width, height)
    frame[y:y + h, x:x + w] = color


def overlay_roi(frame: np.ndarray, name: str, occluder_bgr: np.ndarray | None) -> None:
    if occluder_bgr is None:
        fill_roi(frame, name)
        return
    height, width = frame.shape[:2]
    x, y, w, h = scaled_box(name, width, height)
    patch = cv2.resize(occluder_bgr, (w, h), interpolation=cv2.INTER_AREA)
    frame[y:y + h, x:x + w] = patch


def dim_roi(frame: np.ndarray, name: str, alpha: float = 0.15) -> None:
    height, width = frame.shape[:2]
    x, y, w, h = scaled_box(name, width, height)
    frame[y:y + h, x:x + w] = (frame[y:y + h, x:x + w].astype(np.float32) * alpha).astype(np.uint8)


def load_occluder(path: Path | None) -> np.ndarray | None:
    if path is None:
        return None
    occluder = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if occluder is None:
        raise RuntimeError(f"Failed to read occluder image: {path}")
    return occluder


def mutate_score_hidden(frame: np.ndarray, occluder_bgr: np.ndarray | None = None) -> None:
    overlay_roi(frame, "top_score_bar", occluder_bgr)


def mutate_kill_feed_hidden(frame: np.ndarray, occluder_bgr: np.ndarray | None = None) -> None:
    overlay_roi(frame, "kill_feed_area", occluder_bgr)


def mutate_hud_hidden(frame: np.ndarray, occluder_bgr: np.ndarray | None = None) -> None:
    for name in ["hp_ac_area", "weapon_ammo_area", "crosshair", "minimap"]:
        if occluder_bgr is None:
            dim_roi(frame, name, alpha=0.05)
        else:
            overlay_roi(frame, name, occluder_bgr)


def mutation_for_case(case: str, occluder_bgr: np.ndarray | None = None) -> Callable[[np.ndarray], None]:
    if case == "control_pass":
        return lambda frame: None
    if case == "score_hidden_fail":
        return lambda frame: mutate_score_hidden(frame, occluder_bgr)
    if case == "kill_feed_hidden_observe":
        return lambda frame: mutate_kill_feed_hidden(frame, occluder_bgr)
    if case == "hud_hidden_respawn_fail":
        return lambda frame: mutate_hud_hidden(frame, occluder_bgr)
    raise ValueError(f"Unknown case: {case}")


def write_mutated_video(
    src: Path,
    dst: Path,
    case: str,
    max_seconds: float | None = None,
    occluder_bgr: np.ndarray | None = None,
) -> dict:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {src}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    max_frames = frame_count
    if max_seconds is not None and max_seconds > 0:
        max_frames = min(max_frames, int(round(max_seconds * fps)))

    dst.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Failed to open writer: {dst}")

    mutate = mutation_for_case(case, occluder_bgr=occluder_bgr)
    written = 0
    while written < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        mutate(frame)
        writer.write(frame)
        written += 1

    cap.release()
    writer.release()
    return {
        "source": str(src),
        "output": str(dst),
        "case": case,
        "width": width,
        "height": height,
        "fps": fps,
        "source_frame_count": frame_count,
        "written_frame_count": written,
        "duration_sec": written / fps if fps > 0 else 0.0,
    }


def copy_control(src: Path, dst: Path) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    cap = cv2.VideoCapture(str(dst))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {
        "source": str(src),
        "output": str(dst),
        "case": "control_pass",
        "width": width,
        "height": height,
        "fps": fps,
        "source_frame_count": frame_count,
        "written_frame_count": frame_count,
        "duration_sec": frame_count / fps if fps > 0 else 0.0,
    }


def case_list(value: str) -> list[str]:
    if value == "all":
        return list(CASES)
    out = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [case for case in out if case not in CASES]
    if unknown:
        raise ValueError(f"Unknown case(s): {unknown}. Available: {sorted(CASES)}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Create synthetic CrossFire QA videos from an FHD dataset.")
    parser.add_argument("--source-dataset", default="fhd_dataset")
    parser.add_argument("--out", default="synthetic_fhd_dataset")
    parser.add_argument("--cases", default="all", help="Comma-separated cases, or 'all'.")
    parser.add_argument("--limit-videos", type=int, default=0, help="0 means all videos.")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="Trim mutated videos for faster tests; 0 keeps full length.")
    parser.add_argument(
        "--occluder-image",
        default="",
        help="Optional image used to cover mutated ROIs. Empty keeps the previous black/dim occlusion.",
    )
    args = parser.parse_args()

    source_dataset = Path(args.source_dataset)
    out_root = Path(args.out)
    videos = discover_videos(source_dataset)
    if args.limit_videos > 0:
        videos = videos[: args.limit_videos]
    cases = case_list(args.cases)
    max_seconds = args.max_seconds if args.max_seconds > 0 else None
    occluder_path = Path(args.occluder_image) if args.occluder_image else None
    occluder_bgr = load_occluder(occluder_path)

    manifest = {
        "source_dataset": str(source_dataset.resolve()),
        "out": str(out_root.resolve()),
        "occluder_image": str(occluder_path.resolve()) if occluder_path else None,
        "occlusion_mode": "image_overlay" if occluder_bgr is not None else "black_or_dim_roi",
        "cases": {name: asdict(CASES[name]) for name in cases},
        "videos": [],
    }

    for src in videos:
        safe_stem = src.stem.replace(" ", "_")
        for case in cases:
            dst = out_root / case / f"{safe_stem}__{case}.mp4"
            print(f"[{case}] {src.name} -> {dst}", flush=True)
            if case == "control_pass" and max_seconds is None:
                record = copy_control(src, dst)
            else:
                record = write_mutated_video(src, dst, case, max_seconds=max_seconds, occluder_bgr=occluder_bgr)
            record["expected_behavior"] = CASES[case].expected_behavior
            record["description"] = CASES[case].description
            manifest["videos"].append(record)

    manifest_path = out_root / "synthetic_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "num_videos": len(manifest["videos"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
