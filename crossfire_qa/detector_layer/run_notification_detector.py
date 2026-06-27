from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from notification_detector import (
    NotificationDetector,
    NotificationTemporalAggregator,
    frame_readings_to_dict,
    load_count_change_events,
    load_notification_config,
)
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


NOTIFICATION_ROIS = [
    "top_score_bar",
    "kill_feed_area",
    "hp_ac_area",
    "weapon_ammo_area",
    "crosshair",
]


def is_gameplay_score_bar(top_score_crop_bgr) -> bool:
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


def _resolve_path(path_value: str, base_dir: Path) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    if p.exists():
        return p
    return base_dir / p


def iter_crops_from_ui_report(ui_report_path: str | Path, include_non_gameplay: bool = False) -> Iterator[tuple[dict[str, object], int, float, dict[str, str]]]:
    ui_report_path = Path(ui_report_path)
    with ui_report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)

    base_dir = ui_report_path.parent
    for det in report.get("detections", []):
        crops: dict[str, object] = {}
        crop_paths: dict[str, str] = {}
        regions = det.get("regions", {})
        for roi_name in NOTIFICATION_ROIS:
            region = regions.get(roi_name)
            if not region:
                continue
            crop_path = region.get("crop_path")
            if not crop_path:
                continue
            p = _resolve_path(crop_path, base_dir)
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                print(f"[WARN] failed to read crop: {p}", file=sys.stderr)
                continue
            crops[roi_name] = img
            crop_paths[roi_name] = str(p)
        if not crops:
            print(
                "[WARN] no notification crops found. Re-run run_ui_detection.py with --save-crops, or use --video mode.",
                file=sys.stderr,
            )
            continue
        if not include_non_gameplay and not is_gameplay_score_bar(crops.get("top_score_bar")):
            continue
        yield crops, int(det.get("frame_index", -1)), float(det.get("timestamp_sec", 0.0)), crop_paths


def save_debug_crops(crops: dict[str, object], crop_paths: dict[str, str], out_dir: Path, frame_index: int, timestamp_sec: float) -> dict[str, str]:
    crop_root = out_dir / "debug_notification_crops" / _frame_stem(frame_index, timestamp_sec)
    crop_root.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    for name, crop in crops.items():
        path = crop_root / f"{name}.jpg"
        cv2.imwrite(str(path), crop)  # type: ignore[arg-type]
        saved[name] = str(path)
    crop_paths.update(saved)
    return crop_paths


def run_from_ui_report(args: argparse.Namespace, detector: NotificationDetector) -> list:
    frame_readings = []
    for i, (crops, frame_index, timestamp_sec, crop_paths) in enumerate(
        iter_crops_from_ui_report(args.ui_report, include_non_gameplay=args.include_non_gameplay)
    ):
        if args.max_frames is not None and i >= args.max_frames:
            break
        frame_readings.append(
            detector.detect_frame(
                crops,  # type: ignore[arg-type]
                frame_index=frame_index,
                timestamp_sec=timestamp_sec,
                crop_paths=crop_paths,
            )
        )
    return frame_readings


def run_from_video(args: argparse.Namespace, detector: NotificationDetector) -> list:
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

    frame_readings = []
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
            if not args.include_non_gameplay and not is_gameplay_score_bar(all_crops.get("top_score_bar")):
                continue
            crops = {name: all_crops[name] for name in NOTIFICATION_ROIS if name in all_crops}
            crop_paths: dict[str, str] = {}
            if args.save_debug_crops:
                crop_paths = save_debug_crops(crops, crop_paths, Path(args.out), packet.frame_index, packet.timestamp_sec)
            frame_readings.append(
                detector.detect_frame(
                    crops,
                    frame_index=packet.frame_index,
                    timestamp_sec=packet.timestamp_sec,
                    crop_paths=crop_paths,
                )
            )
    return frame_readings


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=str, help="input mp4 path; internally calls MP4FrameSampler + CrossFireUIDetector")
    src.add_argument("--ui-report", type=str, help="ui_detection_report.json generated by run_ui_detection.py with --save-crops")

    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0, help="only used with --video")
    parser.add_argument("--resize", type=str, default="1920x1080", help="only used with --video")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--roi-config", type=str, default=None)
    parser.add_argument("--ui-templates", type=str, default=None, help="optional UI templates for anchor correction")
    parser.add_argument("--notification-templates", type=str, default=None, help="optional notification template directory")
    parser.add_argument("--notification-config", type=str, default=None)
    parser.add_argument("--kill-count-report", type=str, default=None, help="optional kill_count_report.json for count cross-check")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--no-heuristics", action="store_true", help="disable non-template fallback signals")
    parser.add_argument("--include-non-gameplay", action="store_true", help="do not filter menu/loading frames")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--save-debug-crops", action="store_true", help="only used with --video")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    class_thresholds, template_thresholds, temporal_cfg = load_notification_config(args.notification_config)
    detector = NotificationDetector(
        template_dir=args.notification_templates,
        class_thresholds=class_thresholds,
        template_thresholds=template_thresholds,
        use_heuristics=not args.no_heuristics,
    )

    if args.video:
        frame_readings = run_from_video(args, detector)
        source = {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize}
    else:
        frame_readings = run_from_ui_report(args, detector)
        source = {"type": "ui_report", "path": args.ui_report}

    count_events = load_count_change_events(args.kill_count_report)
    aggregator = NotificationTemporalAggregator(
        class_thresholds=class_thresholds,
        merge_gap_sec=float(temporal_cfg.get("merge_gap_sec", 1.0)),
        min_votes=int(temporal_cfg.get("min_votes", 2)),
        kill_signal_window_sec=float(temporal_cfg.get("kill_signal_window_sec", 2.0)),
        death_respawn_window_sec=float(temporal_cfg.get("death_respawn_window_sec", 5.0)),
        count_match_window_sec=float(temporal_cfg.get("count_match_window_sec", 2.0)),
        allow_medal_only_kill=bool(temporal_cfg.get("allow_medal_only_kill", False)),
    )
    aggregate = aggregator.aggregate(frame_readings, count_change_events=count_events)

    report = {
        "source": source,
        "detector": {
            "notification_templates": args.notification_templates,
            "available_notification_templates": detector.template_lib.available_keys(),
            "class_thresholds": class_thresholds,
            "template_thresholds": template_thresholds,
            "use_heuristics": not args.no_heuristics,
        },
        "crosscheck": {
            "kill_count_report": args.kill_count_report,
            "num_count_change_events": len(count_events),
        },
        "num_frame_readings": len(frame_readings),
        "frame_readings": frame_readings_to_dict(frame_readings),
        "temporal_aggregation": aggregate,
    }

    report_path = out_dir / "notification_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    event_counts: dict[str, int] = {}
    for ev in aggregate["events"]:
        event_counts[ev["event"]] = event_counts.get(ev["event"], 0) + 1

    print(json.dumps({
        "report_path": str(report_path),
        "num_frame_readings": len(frame_readings),
        "event_counts": event_counts,
        "available_notification_templates": detector.template_lib.available_keys(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
