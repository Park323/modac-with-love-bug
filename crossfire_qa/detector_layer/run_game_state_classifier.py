from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator, Optional

import cv2

from game_state_classifier import (
    STATE_ROIS,
    GameStateClassifier,
    GameStateTemporalAggregator,
    frame_state_readings_to_dict,
    load_notification_hints,
    load_state_config,
    nearest_notification_hint,
)
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


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


def iter_crops_from_ui_report(ui_report_path: str | Path) -> Iterator[tuple[dict[str, object], int, float, dict[str, str]]]:
    ui_report_path = Path(ui_report_path)
    with ui_report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)

    base_dir = ui_report_path.parent
    for det in report.get("detections", []):
        crops: dict[str, object] = {}
        crop_paths: dict[str, str] = {}
        regions = det.get("regions", {})
        for roi_name in STATE_ROIS:
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
                "[WARN] no game-state crops found. Re-run run_ui_detection.py with --save-crops, or use --video mode.",
                file=sys.stderr,
            )
            continue
        yield crops, int(det.get("frame_index", -1)), float(det.get("timestamp_sec", 0.0)), crop_paths


def save_debug_crops(crops: dict[str, object], crop_paths: dict[str, str], out_dir: Path, frame_index: int, timestamp_sec: float) -> dict[str, str]:
    crop_root = out_dir / "debug_state_crops" / _frame_stem(frame_index, timestamp_sec)
    crop_root.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    for name, crop in crops.items():
        path = crop_root / f"{name}.jpg"
        cv2.imwrite(str(path), crop)  # type: ignore[arg-type]
        saved[name] = str(path)
    crop_paths.update(saved)
    return crop_paths


def run_from_ui_report(args: argparse.Namespace, classifier: GameStateClassifier, notification_hints: list[dict]) -> list:
    frame_readings = []
    for i, (crops, frame_index, timestamp_sec, crop_paths) in enumerate(iter_crops_from_ui_report(args.ui_report)):
        if args.max_frames is not None and i >= args.max_frames:
            break
        hint = nearest_notification_hint(notification_hints, frame_index, timestamp_sec, args.notification_hint_window_sec)
        frame_readings.append(
            classifier.detect_frame(
                crops=crops,  # type: ignore[arg-type]
                frame_index=frame_index,
                timestamp_sec=timestamp_sec,
                crop_paths=crop_paths,
                notification_hint=hint,
            )
        )
    return frame_readings


def run_from_video(args: argparse.Namespace, classifier: GameStateClassifier, notification_hints: list[dict]) -> list:
    frame_readings = []
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

    out_dir = Path(args.out)
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
            crops = {k: v for k, v in all_crops.items() if k in STATE_ROIS}
            crop_paths: dict[str, str] = {}
            if args.save_debug_crops:
                crop_paths = save_debug_crops(crops, crop_paths, out_dir, packet.frame_index, packet.timestamp_sec)
            hint = nearest_notification_hint(notification_hints, packet.frame_index, packet.timestamp_sec, args.notification_hint_window_sec)
            frame_readings.append(
                classifier.detect_frame(
                    crops=crops,
                    frame_index=packet.frame_index,
                    timestamp_sec=packet.timestamp_sec,
                    crop_paths=crop_paths,
                    notification_hint=hint,
                )
            )
    return frame_readings


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=str, help="input mp4; calls MP4FrameSampler + CrossFireUIDetector")
    src.add_argument("--ui-report", type=str, help="ui_detection_report.json generated with --save-crops")

    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0, help="only used with --video")
    parser.add_argument("--resize", type=str, default="1920x1080", help="sampler resize; only used with --video")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--roi-config", type=str, default=None)
    parser.add_argument("--ui-templates", type=str, default=None, help="optional UI anchor/region templates")
    parser.add_argument("--state-templates", type=str, default=None, help="optional game state templates")
    parser.add_argument("--state-config", type=str, default=None, help="optional game_state_config JSON")
    parser.add_argument("--notification-report", type=str, default=None, help="optional notification_report.json for cross-check/hints")
    parser.add_argument("--notification-hint-window-sec", type=float, default=0.35)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--save-debug-crops", action="store_true", help="only used with --video")
    parser.add_argument("--no-heuristics", action="store_true")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    state_thresholds, feature_thresholds, temporal_cfg = load_state_config(args.state_config)
    classifier = GameStateClassifier(
        template_dir=args.state_templates,
        state_thresholds=state_thresholds,
        feature_thresholds=feature_thresholds,
        use_heuristics=not args.no_heuristics,
    )
    notification_hints = load_notification_hints(args.notification_report)

    if args.video:
        frame_readings = run_from_video(args, classifier, notification_hints)
        source = {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize}
    else:
        frame_readings = run_from_ui_report(args, classifier, notification_hints)
        source = {"type": "ui_report", "path": args.ui_report}

    aggregator = GameStateTemporalAggregator(
        state_thresholds=state_thresholds,
        smoothing_window_sec=float(temporal_cfg.get("smoothing_window_sec", 0.8)),
        merge_gap_sec=float(temporal_cfg.get("merge_gap_sec", 0.8)),
        min_segment_duration_sec=float(temporal_cfg.get("min_segment_duration_sec", 0.2)),
        respawn_after_death_window_sec=float(temporal_cfg.get("respawn_after_death_window_sec", 8.0)),
        stable_alive_after_respawn_sec=float(temporal_cfg.get("stable_alive_after_respawn_sec", 0.5)),
    )
    aggregate = aggregator.aggregate(frame_readings)

    report = {
        "source": source,
        "classifier": {
            "state_templates": args.state_templates,
            "available_state_templates": classifier.template_lib.available_keys(),
            "state_thresholds": state_thresholds,
            "feature_thresholds": feature_thresholds,
            "use_heuristics": not args.no_heuristics,
        },
        "crosscheck": {
            "notification_report": args.notification_report,
            "num_notification_hints": len(notification_hints),
        },
        "num_frame_readings": len(frame_readings),
        "frame_readings": frame_state_readings_to_dict(frame_readings),
        "temporal_aggregation": aggregate,
    }

    report_path = out_dir / "game_state_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "report_path": str(report_path),
        "num_frame_readings": len(frame_readings),
        "num_segments": len(aggregate.get("segments", [])),
        "num_events": len(aggregate.get("events", [])),
        "out_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
