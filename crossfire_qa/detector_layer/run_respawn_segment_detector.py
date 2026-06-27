"""
상태	         의미
CONFIRMED	    death segment와 alive 복귀 segment가 모두 안정적이고 confidence가 높음
INFERRED	    death→alive 흐름은 보이지만 confidence가 다소 낮음
UNCERTAIN	    일부 신호는 있으나 구간이 짧거나 중간 상태가 불안정함
MISSING	        death 이후 respawn 복귀를 찾지 못함
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

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
from respawn_segment_detector import RespawnSegmentDetector, load_json, load_respawn_config
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


def _resolve_path(path_value: str, base_dir: Path) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    if p.exists():
        return p
    return base_dir / p


def iter_crops_from_ui_report(ui_report_path: str | Path):
    ui_report_path = Path(ui_report_path)
    with ui_report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)

    base_dir = ui_report_path.parent
    for det in report.get("detections", []):
        crops = {}
        crop_paths = {}
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
        if crops:
            yield crops, int(det.get("frame_index", -1)), float(det.get("timestamp_sec", 0.0)), crop_paths


def build_game_state_from_ui_report(args: argparse.Namespace) -> dict:
    state_thresholds, feature_thresholds, temporal_cfg = load_state_config(args.state_config)
    classifier = GameStateClassifier(
        template_dir=args.state_templates,
        state_thresholds=state_thresholds,
        feature_thresholds=feature_thresholds,
        use_heuristics=not args.no_heuristics,
    )
    notification_hints = load_notification_hints(args.notification_report)

    frame_readings = []
    for i, (crops, frame_index, timestamp_sec, crop_paths) in enumerate(iter_crops_from_ui_report(args.ui_report)):
        if args.max_frames is not None and i >= args.max_frames:
            break
        hint = nearest_notification_hint(notification_hints, frame_index, timestamp_sec, args.notification_hint_window_sec)
        frame_readings.append(
            classifier.detect_frame(
                crops=crops,
                frame_index=frame_index,
                timestamp_sec=timestamp_sec,
                crop_paths=crop_paths,
                notification_hint=hint,
            )
        )

    aggregator = GameStateTemporalAggregator(
        state_thresholds=state_thresholds,
        smoothing_window_sec=float(temporal_cfg.get("smoothing_window_sec", 0.8)),
        merge_gap_sec=float(temporal_cfg.get("merge_gap_sec", 0.8)),
        min_segment_duration_sec=float(temporal_cfg.get("min_segment_duration_sec", 0.2)),
        respawn_after_death_window_sec=float(temporal_cfg.get("respawn_after_death_window_sec", 8.0)),
        stable_alive_after_respawn_sec=float(temporal_cfg.get("stable_alive_after_respawn_sec", 0.5)),
    )
    aggregate = aggregator.aggregate(frame_readings)
    return {
        "source": {"type": "ui_report", "path": args.ui_report},
        "num_frame_readings": len(frame_readings),
        "frame_readings": frame_state_readings_to_dict(frame_readings),
        "temporal_aggregation": aggregate,
    }


def build_game_state_from_video(args: argparse.Namespace) -> dict:
    state_thresholds, feature_thresholds, temporal_cfg = load_state_config(args.state_config)
    classifier = GameStateClassifier(
        template_dir=args.state_templates,
        state_thresholds=state_thresholds,
        feature_thresholds=feature_thresholds,
        use_heuristics=not args.no_heuristics,
    )
    notification_hints = load_notification_hints(args.notification_report)

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
            crops = {k: v for k, v in all_crops.items() if k in STATE_ROIS}
            hint = nearest_notification_hint(notification_hints, packet.frame_index, packet.timestamp_sec, args.notification_hint_window_sec)
            frame_readings.append(
                classifier.detect_frame(
                    crops=crops,
                    frame_index=packet.frame_index,
                    timestamp_sec=packet.timestamp_sec,
                    crop_paths={},
                    notification_hint=hint,
                )
            )

    aggregator = GameStateTemporalAggregator(
        state_thresholds=state_thresholds,
        smoothing_window_sec=float(temporal_cfg.get("smoothing_window_sec", 0.8)),
        merge_gap_sec=float(temporal_cfg.get("merge_gap_sec", 0.8)),
        min_segment_duration_sec=float(temporal_cfg.get("min_segment_duration_sec", 0.2)),
        respawn_after_death_window_sec=float(temporal_cfg.get("respawn_after_death_window_sec", 8.0)),
        stable_alive_after_respawn_sec=float(temporal_cfg.get("stable_alive_after_respawn_sec", 0.5)),
    )
    aggregate = aggregator.aggregate(frame_readings)
    return {
        "source": {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize},
        "num_frame_readings": len(frame_readings),
        "frame_readings": frame_state_readings_to_dict(frame_readings),
        "temporal_aggregation": aggregate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--game-state-report", type=str, help="game_state_report.json from run_game_state_classifier.py")
    src.add_argument("--ui-report", type=str, help="ui_detection_report.json generated with --save-crops; game state is built internally")
    src.add_argument("--video", type=str, help="input mp4; sampler + UI detector + game state classifier are run internally")

    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--notification-report", type=str, default=None, help="optional notification_report.json for death panel cross-check")
    parser.add_argument("--respawn-config", type=str, default=None)

    # Options used only when building game-state internally.
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--resize", type=str, default="1920x1080")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--roi-config", type=str, default=None)
    parser.add_argument("--ui-templates", type=str, default=None)
    parser.add_argument("--state-templates", type=str, default=None)
    parser.add_argument("--state-config", type=str, default=None)
    parser.add_argument("--notification-hint-window-sec", type=float, default=0.35)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--no-heuristics", action="store_true")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--save-intermediate-game-state", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.game_state_report:
        game_state_report = load_json(args.game_state_report)
        source = {"type": "game_state_report", "path": args.game_state_report}
    elif args.ui_report:
        game_state_report = build_game_state_from_ui_report(args)
        source = {"type": "ui_report", "path": args.ui_report}
    else:
        game_state_report = build_game_state_from_video(args)
        source = {"type": "video", "path": args.video}

    notification_report = load_json(args.notification_report) if args.notification_report else None
    respawn_cfg = load_respawn_config(args.respawn_config)
    detector = RespawnSegmentDetector(respawn_cfg)
    aggregate = detector.detect(game_state_report, notification_report=notification_report)

    if args.save_intermediate_game_state and not args.game_state_report:
        intermediate_path = out_dir / "intermediate_game_state_report.json"
        with intermediate_path.open("w", encoding="utf-8") as f:
            json.dump(game_state_report, f, ensure_ascii=False, indent=2)
    else:
        intermediate_path = None

    report = {
        "source": source,
        "crosscheck": {
            "notification_report": args.notification_report,
            "used_notification_report": notification_report is not None,
        },
        "game_state_source_stats": {
            "num_frame_readings": game_state_report.get("num_frame_readings", len(game_state_report.get("frame_readings", []))),
            "num_segments": len(game_state_report.get("temporal_aggregation", {}).get("segments", [])),
            "num_events": len(game_state_report.get("temporal_aggregation", {}).get("events", [])),
        },
        "respawn_detection": aggregate,
        "intermediate_game_state_report": str(intermediate_path) if intermediate_path else None,
    }

    report_path = out_dir / "respawn_segment_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    summary = aggregate.get("summary", {})
    print(json.dumps({
        "report_path": str(report_path),
        "summary": summary,
        "intermediate_game_state_report": str(intermediate_path) if intermediate_path else None,
        "out_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
