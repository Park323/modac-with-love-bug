from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from kill_count_reader import (
    KillCountTemporalAggregator,
    TopScoreCountReader,
    frame_readings_to_dict,
    load_kill_feed_events,
    load_score_reader_config,
)
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize
from setup_optional_deps import ensure_easyocr, is_import_available


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
    direct = p
    if direct.exists():
        return direct
    return base_dir / p


def iter_top_score_crops_from_ui_report(ui_report_path: str | Path, include_non_gameplay: bool = False) -> Iterator[tuple[object, int, float, str]]:
    ui_report_path = Path(ui_report_path)
    with ui_report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)

    base_dir = ui_report_path.parent
    for det in report.get("detections", []):
        region = det.get("regions", {}).get("top_score_bar")
        if not region:
            continue
        crop_path = region.get("crop_path")
        if not crop_path:
            print(
                "[WARN] top_score_bar crop_path missing. Re-run run_ui_detection.py with --save-crops, "
                "or use --video mode.",
                file=sys.stderr,
            )
            continue
        p = _resolve_path(crop_path, base_dir)
        crop = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if crop is None:
            print(f"[WARN] failed to read crop: {p}", file=sys.stderr)
            continue
        if not include_non_gameplay and not is_gameplay_score_bar(crop):
            continue
        yield crop, int(det.get("frame_index", -1)), float(det.get("timestamp_sec", 0.0)), str(p)


def run_from_ui_report(args: argparse.Namespace, reader: TopScoreCountReader) -> list:
    frame_readings = []
    last_score_read_ts: Optional[float] = None
    for i, (crop, frame_index, timestamp_sec, crop_path) in enumerate(
        iter_top_score_crops_from_ui_report(args.ui_report, include_non_gameplay=args.include_non_gameplay)
    ):
        if args.max_frames is not None and i >= args.max_frames:
            break
        sample_interval = args.vlm_sample_interval_sec if args.backend == "vlm" else args.score_sample_interval_sec
        if sample_interval > 0 and last_score_read_ts is not None:
            if timestamp_sec - last_score_read_ts < sample_interval:
                continue
        frame_readings.append(
            reader.read_top_score_bar(
                crop,
                frame_index=frame_index,
                timestamp_sec=timestamp_sec,
                crop_path=crop_path,
            )
        )
        last_score_read_ts = timestamp_sec
    return frame_readings


def run_from_video(args: argparse.Namespace, reader: TopScoreCountReader) -> list:
    if args.roi_config:
        detector = CrossFireUIDetector.from_json(
            args.roi_config,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )
    else:
        base_resolution = parse_resize(args.base_resolution)
        assert base_resolution is not None
        detector = CrossFireUIDetector(
            base_resolution=base_resolution,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )

    frame_readings = []
    last_score_read_ts: Optional[float] = None
    with MP4FrameSampler(
        video_path=args.video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for i, packet in enumerate(sampler.iter_frames()):
            if args.max_frames is not None and i >= args.max_frames:
                break
            ui_result = detector.detect(packet.frame, frame_index=packet.frame_index, timestamp_sec=packet.timestamp_sec)
            crops = detector.crop_regions(packet.frame, ui_result)
            top_score_crop = crops["top_score_bar"]
            if not args.include_non_gameplay and not is_gameplay_score_bar(top_score_crop):
                continue
            sample_interval = args.vlm_sample_interval_sec if args.backend == "vlm" else args.score_sample_interval_sec
            if sample_interval > 0 and last_score_read_ts is not None:
                if packet.timestamp_sec - last_score_read_ts < sample_interval:
                    continue

            crop_path = None
            if args.save_debug_crops:
                crop_dir = Path(args.out) / "debug_top_score_crops"
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop_path = str(crop_dir / f"{_frame_stem(packet.frame_index, packet.timestamp_sec)}_top_score_bar.jpg")
                cv2.imwrite(crop_path, top_score_crop)

            frame_readings.append(
                reader.read_top_score_bar(
                    top_score_crop,
                    frame_index=packet.frame_index,
                    timestamp_sec=packet.timestamp_sec,
                    crop_path=crop_path,
                )
            )
            last_score_read_ts = packet.timestamp_sec
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
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")

    parser.add_argument("--score-config", type=str, default=None, help="JSON with score_sub_rois and side_map")
    parser.add_argument("--digit-templates", type=str, default=None, help="digit template directory, e.g. digit_templates/0/*.png")
    parser.add_argument("--backend", type=str, default="auto", choices=["auto", "template", "easyocr", "paddleocr", "vlm"])
    parser.add_argument("--auto-install-easyocr", action="store_true", help="attempt pip install easyocr if fallback OCR is requested and unavailable")
    parser.add_argument("--user-install", action="store_true", help="use pip install --user with --auto-install-easyocr")
    parser.add_argument("--easyocr-model-dir", type=str, default=None, help="directory for EasyOCR model files")
    parser.add_argument("--vlm-api-key", type=str, default=None, help="VLM API key; defaults to VLM_API_KEY or OPENAI_API_KEY")
    parser.add_argument("--vlm-api-key-file", type=str, default=None, help="file containing the VLM API key; safer than passing the key in argv")
    parser.add_argument("--vlm-model", type=str, default="gpt-4o-mini")
    parser.add_argument("--vlm-base-url", type=str, default="https://api.openai.com/v1")
    parser.add_argument("--vlm-sample-interval-sec", type=float, default=1.0, help="minimum seconds between VLM score reads")
    parser.add_argument("--score-sample-interval-sec", type=float, default=0.0, help="minimum seconds between non-VLM score reads")
    parser.add_argument("--min-digit-confidence", type=float, default=0.55)

    parser.add_argument("--window-sec", type=float, default=0.8)
    parser.add_argument("--min-read-confidence", type=float, default=0.45)
    parser.add_argument("--min-votes", type=int, default=2)
    parser.add_argument("--max-valid-jump", type=int, default=1)
    parser.add_argument("--kill-feed-events", type=str, default=None, help="optional JSON events for cross-check")
    parser.add_argument("--kill-feed-match-window-sec", type=float, default=2.0)

    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--include-non-gameplay", action="store_true", help="do not filter menu/loading frames")
    parser.add_argument("--save-debug-crops", action="store_true", help="only used with --video")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    score_sub_rois, side_map = load_score_reader_config(args.score_config)

    if args.auto_install_easyocr and args.backend in {"auto", "easyocr"} and not is_import_available("easyocr"):
        install_status = ensure_easyocr(user=args.user_install)
        if not install_status.available_after:
            print(f"[WARN] easyocr install failed: {install_status.error}", file=sys.stderr)
    if args.vlm_api_key is None and args.vlm_api_key_file:
        args.vlm_api_key = Path(args.vlm_api_key_file).read_text(encoding="utf-8").strip()
    if args.vlm_api_key is None:
        args.vlm_api_key = os.environ.get("VLM_API_KEY") or os.environ.get("OPENAI_API_KEY")

    reader = TopScoreCountReader(
        digit_template_dir=args.digit_templates,
        score_sub_rois=score_sub_rois,
        side_map=side_map,
        backend=args.backend,
        min_digit_confidence=args.min_digit_confidence,
        easyocr_model_dir=args.easyocr_model_dir,
        vlm_api_key=args.vlm_api_key,
        vlm_model=args.vlm_model,
        vlm_base_url=args.vlm_base_url,
    )

    if args.video:
        frame_readings = run_from_video(args, reader)
        source = {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize}
    else:
        frame_readings = run_from_ui_report(args, reader)
        source = {"type": "ui_report", "path": args.ui_report}

    kill_feed_events = load_kill_feed_events(args.kill_feed_events)
    aggregator = KillCountTemporalAggregator(
        window_sec=args.window_sec,
        min_read_confidence=args.min_read_confidence,
        min_votes=args.min_votes,
        max_valid_jump=args.max_valid_jump,
        kill_feed_match_window_sec=args.kill_feed_match_window_sec,
    )
    aggregate = aggregator.aggregate(frame_readings, kill_feed_events=kill_feed_events)

    report = {
        "source": source,
        "reader": {
            "backend": args.backend,
            "digit_templates": args.digit_templates,
            "available_digit_templates": reader.template_ocr.library.available_digits(),
            "score_sub_rois": score_sub_rois,
            "side_map": side_map,
            "min_digit_confidence": args.min_digit_confidence,
            "score_sample_interval_sec": args.score_sample_interval_sec,
            "vlm_model": args.vlm_model if args.backend == "vlm" else None,
            "vlm_base_url": args.vlm_base_url if args.backend == "vlm" else None,
            "vlm_sample_interval_sec": args.vlm_sample_interval_sec if args.backend == "vlm" else None,
            "vlm_available": bool(reader.vlm and reader.vlm.available),
            "vlm_error": "" if not reader.vlm else reader.vlm.error,
        },
        "num_frame_readings": len(frame_readings),
        "frame_readings": frame_readings_to_dict(frame_readings),
        "temporal_aggregation": aggregate,
    }

    report_path = out_dir / "kill_count_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "report_path": str(report_path),
        "num_frame_readings": len(frame_readings),
        "num_count_change_events": len(aggregate["events"]),
        "available_digit_templates": reader.template_ocr.library.available_digits(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
