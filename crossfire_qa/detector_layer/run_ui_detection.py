"""
처음에는 고정 ROI + template matching으로 시작하고, 
데이터가 쌓이면 YOLO류 UI detector로 확장

Stage 1. Fixed ROI Detection
- 빠름
- 기존 위치에서 바로 찾음

Stage 2. Expanded Search Detection
- fixed ROI보다 넓은 영역 탐색
- UI 위치가 조금 달라도 대응

Stage 3. Full-frame Detector
- template matching 또는 YOLO
- layout이 크게 바뀌는 상황 대응
"""


from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from ui_detector import CrossFireUIDetector, save_detection_artifacts
from video_sampler import MP4FrameSampler, parse_resize


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


def iter_from_metadata(metadata_path: str | Path) -> Iterator[tuple[object, int, float]]:
    metadata_path = Path(metadata_path)
    with metadata_path.open("r", encoding="utf-8") as f:
        metadata = json.load(f)

    base_dir = metadata_path.parent
    for rec in metadata.get("frames", []):
        image_path = Path(rec["image_path"])
        if not image_path.is_absolute():
            image_path = base_dir / image_path
        frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if frame is None:
            print(f"[WARN] failed to read frame: {image_path}", file=sys.stderr)
            continue
        yield frame, int(rec.get("frame_index", -1)), float(rec.get("timestamp_sec", 0.0))


def run_on_video(args: argparse.Namespace, detector: CrossFireUIDetector) -> list[dict]:
    detections: list[dict] = []
    skipped_non_gameplay = 0
    with MP4FrameSampler(
        video_path=args.video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for i, packet in enumerate(sampler.iter_frames()):
            if args.max_frames is not None and i >= args.max_frames:
                break
            result = detector.detect(
                packet.frame,
                frame_index=packet.frame_index,
                timestamp_sec=packet.timestamp_sec,
            )
            crops = detector.crop_regions(packet.frame, result)
            is_gameplay = is_gameplay_score_bar(crops.get("top_score_bar"))
            if not args.include_non_gameplay and not is_gameplay:
                skipped_non_gameplay += 1
                continue
            stem = _frame_stem(packet.frame_index, packet.timestamp_sec)
            save_detection_artifacts(
                packet.frame,
                detector,
                result,
                args.out,
                stem,
                save_crops=args.save_crops,
                save_overlay=args.save_overlays,
            )
            item = result.to_dict()
            item["is_gameplay_hud"] = is_gameplay
            detections.append(item)
    args.skipped_non_gameplay = skipped_non_gameplay
    return detections


def run_on_metadata(args: argparse.Namespace, detector: CrossFireUIDetector) -> list[dict]:
    detections: list[dict] = []
    skipped_non_gameplay = 0
    for i, (frame, frame_index, timestamp_sec) in enumerate(iter_from_metadata(args.metadata)):
        if args.max_frames is not None and i >= args.max_frames:
            break
        result = detector.detect(frame, frame_index=frame_index, timestamp_sec=timestamp_sec)
        crops = detector.crop_regions(frame, result)
        is_gameplay = is_gameplay_score_bar(crops.get("top_score_bar"))
        if not args.include_non_gameplay and not is_gameplay:
            skipped_non_gameplay += 1
            continue
        stem = _frame_stem(frame_index, timestamp_sec)
        save_detection_artifacts(
            frame,
            detector,
            result,
            args.out,
            stem,
            save_crops=args.save_crops,
            save_overlay=args.save_overlays,
        )
        item = result.to_dict()
        item["is_gameplay_hud"] = is_gameplay
        detections.append(item)
    args.skipped_non_gameplay = skipped_non_gameplay
    return detections


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=str, help="input mp4 path; calls MP4FrameSampler directly")
    src.add_argument("--metadata", type=str, help="metadata.json generated by video_sampler.py")

    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0, help="only used with --video")
    parser.add_argument("--resize", type=str, default="1920x1080", help="sampler resize, e.g. 1920x1080; only used with --video")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--templates", type=str, default=None, help="optional template directory")
    parser.add_argument("--roi-config", type=str, default=None, help="optional JSON config with base_resolution/rois/anchors")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--save-crops", action="store_true")
    parser.add_argument("--save-overlays", action="store_true")
    parser.add_argument("--include-non-gameplay", action="store_true", help="also save fixed ROI crops from menu/loading/non-HUD frames")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--no-template-verify", action="store_true")
    args = parser.parse_args()

    base_resolution = parse_resize(args.base_resolution)
    assert base_resolution is not None

    if args.roi_config:
        detector = CrossFireUIDetector.from_json(
            args.roi_config,
            template_dir=args.templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
            verify_region_templates=not args.no_template_verify,
        )
    else:
        detector = CrossFireUIDetector(
            base_resolution=base_resolution,
            template_dir=args.templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
            verify_region_templates=not args.no_template_verify,
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.video:
        detections = run_on_video(args, detector)
        source = {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize}
    else:
        detections = run_on_metadata(args, detector)
        source = {"type": "metadata", "path": args.metadata}

    report = {
        "source": source,
        "detector": {
            "base_resolution": list(detector.base_resolution),
            "normalize_to_base": detector.normalize_to_base,
            "apply_anchor_correction": detector.apply_anchor_correction,
            "verify_region_templates": detector.verify_region_templates,
            "available_templates": detector.template_lib.available_keys(),
        },
        "num_detections": len(detections),
        "num_skipped_non_gameplay": int(getattr(args, "skipped_non_gameplay", 0)),
        "detections": detections,
    }

    report_path = out_dir / "ui_detection_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "report_path": str(report_path),
        "num_detections": len(detections),
        "num_skipped_non_gameplay": int(getattr(args, "skipped_non_gameplay", 0)),
        "out_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
