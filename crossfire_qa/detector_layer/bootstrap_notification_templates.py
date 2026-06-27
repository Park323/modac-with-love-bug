from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from notification_detector import NotificationDetector, load_notification_config
from run_notification_detector import NOTIFICATION_ROIS, iter_crops_from_ui_report
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


TARGET_TO_CLASS = {
    "kill_feed_area": "kill_feed",
    "kill_medal_area": "first_kill_medal",
    "death_killer_panel": "death_killer_panel",
}

CLASS_TO_TEMPLATE_DIR = {
    "kill_feed": "kill_feed",
    "first_kill_medal": "first_kill_medal",
    "death_killer_panel": "death_killer_panel",
}


def is_gameplay_score_bar(top_score_crop_bgr: np.ndarray) -> bool:
    if top_score_crop_bgr is None or top_score_crop_bgr.size == 0:
        return False
    hsv = cv2.cvtColor(top_score_crop_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(top_score_crop_bgr, cv2.COLOR_BGR2GRAY)
    blue_mask = (
        (hsv[:, :, 0] >= 85)
        & (hsv[:, :, 0] <= 115)
        & (hsv[:, :, 1] > 45)
        & (hsv[:, :, 2] > 75)
    )
    warm_mask = (
        (hsv[:, :, 0] >= 8)
        & (hsv[:, :, 0] <= 35)
        & (hsv[:, :, 1] > 35)
        & (hsv[:, :, 2] > 70)
    )
    bright_mask = gray > 95
    white_mask = (hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 105)
    left_half = slice(None), slice(0, max(1, blue_mask.shape[1] // 2))
    right_half = slice(None), slice(max(1, blue_mask.shape[1] // 2), None)
    return (
        float(np.mean(gray)) >= 45.0
        and float(np.mean(bright_mask)) >= 0.08
        and float(np.mean(white_mask)) >= 0.04
        and (
            float(np.mean(blue_mask[left_half])) >= 0.04
            or float(np.mean(blue_mask[right_half])) >= 0.04
            or float(np.mean(warm_mask[left_half])) >= 0.04
            or float(np.mean(warm_mask[right_half])) >= 0.04
        )
    )


def _frame_stem(frame_index: Optional[int], timestamp_sec: Optional[float]) -> str:
    idx = -1 if frame_index is None else frame_index
    ts = 0.0 if timestamp_sec is None else timestamp_sec
    return f"frame_{idx:06d}_t{ts:08.3f}"


def iter_crops_from_video(args: argparse.Namespace) -> Iterator[tuple[dict[str, object], int, float]]:
    if args.roi_config:
        ui_detector = CrossFireUIDetector.from_json(
            args.roi_config,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )
    else:
        base_resolution = parse_resize(args.base_resolution)
        assert base_resolution is not None
        ui_detector = CrossFireUIDetector(
            base_resolution=base_resolution,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )

    with MP4FrameSampler(
        video_path=args.video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for i, packet in enumerate(sampler.iter_frames()):
            if args.max_frames is not None and i >= args.max_frames:
                break
            ui_result = ui_detector.detect(packet.frame, frame_index=packet.frame_index, timestamp_sec=packet.timestamp_sec)
            all_crops = ui_detector.crop_regions(packet.frame, ui_result)
            if not args.include_non_gameplay and not is_gameplay_score_bar(all_crops["top_score_bar"]):
                continue
            crops = {name: all_crops[name] for name in NOTIFICATION_ROIS if name in all_crops}
            yield crops, packet.frame_index, packet.timestamp_sec


def make_contact_sheet(image_paths: list[Path], out_path: Path, thumb_size: tuple[int, int] = (220, 90), cols: int = 4) -> None:
    if not image_paths:
        return
    thumbs = []
    for p in image_paths:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        thumb = cv2.resize(img, thumb_size, interpolation=cv2.INTER_AREA)
        cv2.putText(thumb, p.stem[-18:], (5, thumb_size[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
        thumbs.append(thumb)
    if not thumbs:
        return
    rows = int(np.ceil(len(thumbs) / cols))
    sheet = np.zeros((rows * thumb_size[1], cols * thumb_size[0], 3), dtype=np.uint8)
    for idx, thumb in enumerate(thumbs):
        r = idx // cols
        c = idx % cols
        y = r * thumb_size[1]
        x = c * thumb_size[0]
        sheet[y:y + thumb_size[1], x:x + thumb_size[0]] = thumb
    cv2.imwrite(str(out_path), sheet)


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=str)
    src.add_argument("--ui-report", type=str)

    parser.add_argument("--out", type=str, required=True, help="template root directory to create/update")
    parser.add_argument("--targets", nargs="+", default=["kill_feed_area", "kill_medal_area", "death_killer_panel"], choices=list(TARGET_TO_CLASS.keys()))
    parser.add_argument("--notification-config", type=str, default=None)
    parser.add_argument("--existing-templates", type=str, default=None, help="optional existing templates to score candidates; if omitted, heuristic fallback is used")
    parser.add_argument("--min-score", type=float, default=0.55)
    parser.add_argument("--top-k-per-class", type=int, default=40)
    parser.add_argument(
        "--copy-mode",
        type=str,
        default="selected",
        choices=["selected", "all-candidates", "candidates-only"],
        help=(
            "selected saves crops above min-score as templates; all-candidates promotes every target crop; "
            "candidates-only saves audit candidates without creating template dirs"
        ),
    )

    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--resize", type=str, default="1920x1080")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--roi-config", type=str, default=None)
    parser.add_argument("--ui-templates", type=str, default=None)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--include-non-gameplay", action="store_true", help="do not filter menu/loading frames in video mode")
    parser.add_argument("--max-frames", type=int, default=None)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir = out_dir / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)

    class_thresholds, template_thresholds, _ = load_notification_config(args.notification_config)
    detector = NotificationDetector(
        template_dir=args.existing_templates,
        class_thresholds=class_thresholds,
        template_thresholds=template_thresholds,
        use_heuristics=True,
    )

    if args.video:
        iterator = iter_crops_from_video(args)
        source = {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize}
    else:
        def _iter_report():
            for crops, frame_index, timestamp_sec, _ in iter_crops_from_ui_report(args.ui_report):
                yield crops, frame_index, timestamp_sec
        iterator = _iter_report()
        source = {"type": "ui_report", "path": args.ui_report}

    rows: list[dict] = []
    selected_by_class: dict[str, list[tuple[float, Path, dict]]] = {cls: [] for cls in CLASS_TO_TEMPLATE_DIR}

    for i, (crops, frame_index, timestamp_sec) in enumerate(iterator):
        if args.max_frames is not None and i >= args.max_frames:
            break
        reading = detector.detect_frame(crops, frame_index=frame_index, timestamp_sec=timestamp_sec)  # type: ignore[arg-type]
        by_key = {(s.roi_name, s.class_name): s for s in reading.signals}

        for target_roi in args.targets:
            cls = TARGET_TO_CLASS[target_roi]
            crop = crops.get(target_roi)
            if crop is None:
                continue
            signal = by_key.get((target_roi, cls))
            score = float(signal.confidence) if signal else 0.0
            status = signal.status if signal else "missing_signal"
            method = signal.method if signal else "none"
            stem = f"{cls}_{_frame_stem(frame_index, timestamp_sec)}_{target_roi}_s{score:.3f}.jpg"

            # Save every target crop into candidates for audit; selected templates are copied from here.
            candidate_class_dir = candidate_dir / cls
            candidate_class_dir.mkdir(parents=True, exist_ok=True)
            candidate_path = candidate_class_dir / stem
            cv2.imwrite(str(candidate_path), crop)  # type: ignore[arg-type]

            row = {
                "frame_index": frame_index,
                "timestamp_sec": timestamp_sec,
                "target_roi": target_roi,
                "class_name": cls,
                "score": score,
                "status": status,
                "method": method,
                "candidate_path": str(candidate_path),
            }
            rows.append(row)
            if args.copy_mode == "all-candidates" or (args.copy_mode == "selected" and score >= args.min_score):
                selected_by_class[cls].append((score, candidate_path, row))

    selected_paths: dict[str, list[str]] = {}
    for cls, items in selected_by_class.items():
        items = sorted(items, key=lambda x: x[0], reverse=True)[: args.top_k_per_class]
        template_subdir = out_dir / CLASS_TO_TEMPLATE_DIR[cls]
        template_subdir.mkdir(parents=True, exist_ok=True)
        selected_paths[cls] = []
        for score, candidate_path, row in items:
            dest = template_subdir / candidate_path.name
            if candidate_path.resolve() != dest.resolve():
                shutil.copy2(candidate_path, dest)
            selected_paths[cls].append(str(dest))
        make_contact_sheet([Path(p) for p in selected_paths[cls]], out_dir / f"contact_sheet_{cls}.jpg")

    report = {
        "source": source,
        "min_score": args.min_score,
        "top_k_per_class": args.top_k_per_class,
        "selected_paths": selected_paths,
        "num_candidates": len(rows),
        "rows": rows,
    }
    report_path = out_dir / "notification_template_bootstrap_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    csv_path = out_dir / "notification_template_bootstrap_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["frame_index", "timestamp_sec", "target_roi", "class_name", "score", "status", "method", "candidate_path"])
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps({
        "report_path": str(report_path),
        "csv_path": str(csv_path),
        "selected_counts": {cls: len(paths) for cls, paths in selected_paths.items()},
        "num_candidates": len(rows),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
