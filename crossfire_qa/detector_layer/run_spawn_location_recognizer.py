"""
location text + minimap + visual reference + respawn state confidence
"""


from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from spawn_location_recognizer import (
    SpawnLocationRecognizer,
    load_json,
    load_spawn_location_config,
    spawn_location_event_to_dict,
)
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


def _install_easyocr_if_requested(install: bool) -> None:
    if not install:
        return
    try:
        import easyocr  # type: ignore  # noqa: F401
        return
    except Exception:
        pass
    subprocess.check_call([sys.executable, "-m", "pip", "install", "easyocr"])


def _frame_stem(frame_index: int, timestamp_sec: float) -> str:
    return f"frame_{frame_index:06d}_t{timestamp_sec:08.3f}"


def _resolve_path(path_value: str, base_dir: Path) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    if p.exists():
        return p
    return base_dir / p


def _load_respawn_events(respawn_report_path: str | Path) -> list[dict[str, Any]]:
    report = load_json(respawn_report_path)
    if "respawn_detection" in report:
        events = report.get("respawn_detection", {}).get("respawn_events", [])
    else:
        events = report.get("respawn_events", [])
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("respawn_time") is None:
            continue
        if str(ev.get("result", "")).upper() == "RESPAWN_MISSING":
            continue
        if str(ev.get("status", "")).upper() == "MISSING":
            continue
        out.append(ev)
    return out


def _target_times(respawn_time: float, offsets: list[float]) -> list[float]:
    return [float(respawn_time) + float(o) for o in offsets]


def _nearest_ui_records(ui_report_path: str | Path, target_times: list[float], max_delta_sec: float = 0.65) -> list[dict[str, Any]]:
    ui_report_path = Path(ui_report_path)
    with ui_report_path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    detections = list(report.get("detections", []))
    selected: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    for target in target_times:
        best: Optional[tuple[float, int, dict[str, Any]]] = None
        for idx, rec in enumerate(detections):
            if idx in used_ids:
                continue
            t = float(rec.get("timestamp_sec", 0.0) or 0.0)
            delta = abs(t - target)
            if best is None or delta < best[0]:
                best = (delta, idx, rec)
        if best and best[0] <= max_delta_sec:
            used_ids.add(best[1])
            selected.append(best[2])
    return selected


def _read_crops_from_ui_record(rec: dict[str, Any], base_dir: Path) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    crops: dict[str, np.ndarray] = {}
    crop_paths: dict[str, str] = {}
    for name, region in rec.get("regions", {}).items():
        crop_path = region.get("crop_path")
        if not crop_path:
            continue
        p = _resolve_path(str(crop_path), base_dir)
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            continue
        crops[name] = img
        crop_paths[name] = str(p)
    return crops, crop_paths


def _nearest_metadata_frames(metadata_path: str | Path, target_times: list[float], max_delta_sec: float = 0.65) -> list[dict[str, Any]]:
    metadata_path = Path(metadata_path)
    with metadata_path.open("r", encoding="utf-8") as f:
        metadata = json.load(f)
    records = list(metadata.get("frames", []))
    selected: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    for target in target_times:
        best: Optional[tuple[float, int, dict[str, Any]]] = None
        for idx, rec in enumerate(records):
            if idx in used_ids:
                continue
            t = float(rec.get("timestamp_sec", 0.0) or 0.0)
            delta = abs(t - target)
            if best is None or delta < best[0]:
                best = (delta, idx, rec)
        if best and best[0] <= max_delta_sec:
            used_ids.add(best[1])
            selected.append(best[2])
    return selected


def _read_frame_from_metadata_rec(rec: dict[str, Any], base_dir: Path) -> Optional[np.ndarray]:
    image_path = rec.get("image_path")
    if not image_path:
        return None
    p = _resolve_path(str(image_path), base_dir)
    return cv2.imread(str(p), cv2.IMREAD_COLOR)


def collect_evidence_from_ui_report(
    args: argparse.Namespace,
    recognizer: SpawnLocationRecognizer,
    respawn_event: dict[str, Any],
    expected_spawn: Optional[str],
) -> list[Any]:
    ui_report_path = Path(args.ui_report)
    respawn_time = float(respawn_event.get("respawn_time", 0.0) or 0.0)
    respawn_conf = float(respawn_event.get("confidence", 0.0) or 0.0)
    offsets = recognizer.config.get("sample_offsets_after_respawn_sec", [0.0, 0.4, 0.8])
    records = _nearest_ui_records(ui_report_path, _target_times(respawn_time, offsets), args.max_frame_delta_sec)
    evidences = []
    for rec in records:
        crops, crop_paths = _read_crops_from_ui_record(rec, ui_report_path.parent)
        frame_bgr = None
        if args.frame_metadata:
            # Optional full-frame image is only needed for visual reference matching.
            nearest = _nearest_metadata_frames(args.frame_metadata, [float(rec.get("timestamp_sec", 0.0))], args.max_frame_delta_sec)
            if nearest:
                frame_bgr = _read_frame_from_metadata_rec(nearest[0], Path(args.frame_metadata).parent)
        evidences.append(
            recognizer.analyze_respawn_frame(
                frame_index=int(rec.get("frame_index", -1)),
                timestamp_sec=float(rec.get("timestamp_sec", 0.0) or 0.0),
                respawn_time=respawn_time,
                crops=crops,
                crop_paths=crop_paths,
                frame_bgr=frame_bgr,
                expected_spawn=expected_spawn,
                respawn_confidence=respawn_conf,
            )
        )
    return evidences


def collect_evidence_from_metadata(
    args: argparse.Namespace,
    detector: CrossFireUIDetector,
    recognizer: SpawnLocationRecognizer,
    respawn_event: dict[str, Any],
    expected_spawn: Optional[str],
) -> list[Any]:
    metadata_path = Path(args.frame_metadata)
    respawn_time = float(respawn_event.get("respawn_time", 0.0) or 0.0)
    respawn_conf = float(respawn_event.get("confidence", 0.0) or 0.0)
    offsets = recognizer.config.get("sample_offsets_after_respawn_sec", [0.0, 0.4, 0.8])
    records = _nearest_metadata_frames(metadata_path, _target_times(respawn_time, offsets), args.max_frame_delta_sec)
    evidences = []
    debug_dir = Path(args.out) / "debug_spawn_crops"
    if args.save_debug_crops:
        debug_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        frame = _read_frame_from_metadata_rec(rec, metadata_path.parent)
        if frame is None:
            continue
        frame_index = int(rec.get("frame_index", -1))
        ts = float(rec.get("timestamp_sec", 0.0) or 0.0)
        ui_result = detector.detect(frame, frame_index=frame_index, timestamp_sec=ts)
        crops = detector.crop_regions(frame, ui_result)
        crop_paths: dict[str, str] = {}
        if args.save_debug_crops:
            stem = _frame_stem(frame_index, ts)
            frame_dir = debug_dir / stem
            frame_dir.mkdir(parents=True, exist_ok=True)
            for name in ["location_text", "minimap"]:
                if name in crops:
                    p = frame_dir / f"{name}.jpg"
                    cv2.imwrite(str(p), crops[name])
                    crop_paths[name] = str(p)
        evidences.append(
            recognizer.analyze_respawn_frame(
                frame_index=frame_index,
                timestamp_sec=ts,
                respawn_time=respawn_time,
                crops=crops,
                crop_paths=crop_paths,
                frame_bgr=frame,
                expected_spawn=expected_spawn,
                respawn_confidence=respawn_conf,
            )
        )
    return evidences


def collect_evidence_from_video(
    args: argparse.Namespace,
    detector: CrossFireUIDetector,
    recognizer: SpawnLocationRecognizer,
    respawn_event: dict[str, Any],
    expected_spawn: Optional[str],
) -> list[Any]:
    respawn_time = float(respawn_event.get("respawn_time", 0.0) or 0.0)
    respawn_conf = float(respawn_event.get("confidence", 0.0) or 0.0)
    offsets = recognizer.config.get("sample_offsets_after_respawn_sec", [0.0, 0.4, 0.8])
    target_times = _target_times(respawn_time, offsets)
    selected: dict[float, tuple[float, Any]] = {}

    with MP4FrameSampler(
        video_path=args.video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for packet in sampler.iter_frames():
            for target in target_times:
                delta = abs(packet.timestamp_sec - target)
                if delta > args.max_frame_delta_sec:
                    continue
                prev = selected.get(target)
                if prev is None or delta < prev[0]:
                    selected[target] = (delta, packet)

    evidences = []
    debug_dir = Path(args.out) / "debug_spawn_crops"
    if args.save_debug_crops:
        debug_dir.mkdir(parents=True, exist_ok=True)
    for _, packet in sorted(selected.values(), key=lambda item: item[1].timestamp_sec):
        ui_result = detector.detect(packet.frame, frame_index=packet.frame_index, timestamp_sec=packet.timestamp_sec)
        crops = detector.crop_regions(packet.frame, ui_result)
        crop_paths: dict[str, str] = {}
        if args.save_debug_crops:
            stem = _frame_stem(packet.frame_index, packet.timestamp_sec)
            frame_dir = debug_dir / stem
            frame_dir.mkdir(parents=True, exist_ok=True)
            for name in ["location_text", "minimap"]:
                if name in crops:
                    p = frame_dir / f"{name}.jpg"
                    cv2.imwrite(str(p), crops[name])
                    crop_paths[name] = str(p)
            full_path = frame_dir / "full_frame.jpg"
            cv2.imwrite(str(full_path), packet.frame)
            crop_paths["full_frame"] = str(full_path)
        evidences.append(
            recognizer.analyze_respawn_frame(
                frame_index=packet.frame_index,
                timestamp_sec=packet.timestamp_sec,
                respawn_time=respawn_time,
                crops=crops,
                crop_paths=crop_paths,
                frame_bgr=packet.frame,
                expected_spawn=expected_spawn,
                respawn_confidence=respawn_conf,
            )
        )
    return evidences


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=str, help="input MP4; UI detection is run internally near respawn times")
    src.add_argument("--ui-report", type=str, help="ui_detection_report.json generated with --save-crops")
    src.add_argument("--frame-metadata", type=str, help="metadata.json from video_sampler.py; UI detection is run on saved frames")

    parser.add_argument("--respawn-report", type=str, required=True, help="respawn_segment_report.json")
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--expected-spawn", type=str, default=None, help="expected spawn label, e.g. 'BL Base'")
    parser.add_argument("--spawn-config", type=str, default=None)
    parser.add_argument("--spawn-references", type=str, default=None, help="spawn reference root directory")

    parser.add_argument("--sample-fps", type=float, default=10.0)
    parser.add_argument("--resize", type=str, default="1920x1080")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--roi-config", type=str, default=None)
    parser.add_argument("--ui-templates", type=str, default=None)
    parser.add_argument("--max-frame-delta-sec", type=float, default=0.65)
    parser.add_argument("--max-spawn-checks", type=int, default=0, help="limit respawn events checked; 0 means all events")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--disable-easyocr", action="store_true")
    parser.add_argument("--install-easyocr", action="store_true")
    parser.add_argument("--easyocr-model-dir", type=str, default=None)
    parser.add_argument("--save-debug-crops", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    _install_easyocr_if_requested(args.install_easyocr)

    cfg = load_spawn_location_config(args.spawn_config)
    expected_spawn = args.expected_spawn or cfg.get("expected_spawn")
    recognizer = SpawnLocationRecognizer(
        reference_dir=args.spawn_references,
        config=cfg,
        use_easyocr=not args.disable_easyocr,
        easyocr_model_dir=args.easyocr_model_dir,
    )

    base_resolution = parse_resize(args.base_resolution)
    if args.roi_config:
        detector = CrossFireUIDetector.from_json(
            args.roi_config,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )
    else:
        detector = CrossFireUIDetector(
            base_resolution=base_resolution,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )

    all_respawn_events = _load_respawn_events(args.respawn_report)
    max_checks = max(0, int(args.max_spawn_checks or 0))
    respawn_events = all_respawn_events[:max_checks] if max_checks else all_respawn_events
    spawn_events = []
    for respawn_event in respawn_events:
        if args.ui_report:
            evidences = collect_evidence_from_ui_report(args, recognizer, respawn_event, expected_spawn)
        elif args.frame_metadata:
            evidences = collect_evidence_from_metadata(args, detector, recognizer, respawn_event, expected_spawn)
        else:
            evidences = collect_evidence_from_video(args, detector, recognizer, respawn_event, expected_spawn)
        spawn_events.append(spawn_location_event_to_dict(recognizer.aggregate_event(respawn_event, evidences, expected_spawn=expected_spawn)))

    summary = {
        "num_respawn_events": len(all_respawn_events),
        "num_spawn_checks": len(spawn_events),
        "max_spawn_checks": max_checks,
        "num_skipped_by_max_spawn_checks": max(0, len(all_respawn_events) - len(respawn_events)),
        "num_pass": sum(1 for e in spawn_events if e.get("result") == "PASS"),
        "num_fail": sum(1 for e in spawn_events if e.get("result") == "FAIL"),
        "num_uncertain": sum(1 for e in spawn_events if e.get("result") == "UNCERTAIN"),
        "num_observed": sum(1 for e in spawn_events if e.get("result") == "OBSERVED"),
        "expected_spawn": expected_spawn,
        "available_reference_spawns": recognizer.reference_lib.available_spawns(),
        "easyocr_available": bool(recognizer.ocr_reader and recognizer.ocr_reader.available),
        "easyocr_error": "" if not recognizer.ocr_reader else recognizer.ocr_reader.error,
    }

    report = {
        "source": {
            "video": args.video,
            "ui_report": args.ui_report,
            "frame_metadata": args.frame_metadata,
            "respawn_report": args.respawn_report,
        },
        "config": recognizer.config,
        "summary": summary,
        "spawn_location_events": spawn_events,
    }
    report_path = out_dir / "spawn_location_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({"report_path": str(report_path), "summary": summary, "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
