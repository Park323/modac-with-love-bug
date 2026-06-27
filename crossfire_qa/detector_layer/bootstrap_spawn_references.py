"""
1. respawn_segment_report.json에서 respawn_time을 읽음
2. respawn_time + offset 프레임을 원본 mp4에서 찾음
3. UI Detector로 minimap crop을 자름
4. 그 crop을 spawn_references/BL_Base/minimap/ 아래 저장
"""



from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

import cv2

from spawn_location_recognizer import load_json, load_spawn_location_config
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


def _load_respawn_events(respawn_report_path: str | Path) -> list[dict[str, Any]]:
    report = load_json(respawn_report_path)
    events = report.get("respawn_detection", {}).get("respawn_events", report.get("respawn_events", []))
    return [ev for ev in events if ev.get("respawn_time") is not None and str(ev.get("status", "")).upper() != "MISSING"]


def _frame_stem(frame_index: int, timestamp_sec: float) -> str:
    return f"frame_{frame_index:06d}_t{timestamp_sec:08.3f}"


def _safe_spawn_dir_name(name: str) -> str:
    return name.strip().replace(" ", "_").replace("/", "_")


def collect_from_video(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_spawn_location_config(args.spawn_config)
    offsets = [float(x) for x in args.offsets.split(",") if x.strip()]
    events = _load_respawn_events(args.respawn_report)
    detector = CrossFireUIDetector(
        base_resolution=parse_resize(args.base_resolution),
        template_dir=args.ui_templates,
        normalize_to_base=not args.no_normalize,
        apply_anchor_correction=not args.no_anchor_correction,
    )
    spawn_name = args.spawn_name or cfg.get("expected_spawn")
    if not spawn_name:
        raise ValueError("--spawn-name or expected_spawn in config is required for bootstrapping references")

    out_root = Path(args.out) / _safe_spawn_dir_name(spawn_name)
    visual_dir = out_root / "visual"
    minimap_dir = out_root / "minimap"
    location_dir = out_root / "location_text"
    visual_dir.mkdir(parents=True, exist_ok=True)
    minimap_dir.mkdir(parents=True, exist_ok=True)
    location_dir.mkdir(parents=True, exist_ok=True)

    target_times: list[tuple[float, int, float]] = []
    for ev_idx, ev in enumerate(events):
        rt = float(ev.get("respawn_time", 0.0) or 0.0)
        for offset in offsets:
            target_times.append((rt + offset, ev_idx, rt))

    selected: dict[tuple[int, float], tuple[float, Any, float]] = {}
    with MP4FrameSampler(
        video_path=args.video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for packet in sampler.iter_frames():
            for target_t, ev_idx, rt in target_times:
                delta = abs(packet.timestamp_sec - target_t)
                if delta > args.max_frame_delta_sec:
                    continue
                key = (ev_idx, target_t)
                prev = selected.get(key)
                if prev is None or delta < prev[0]:
                    selected[key] = (delta, packet, rt)

    records = []
    for (_, target_t), (_, packet, rt) in sorted(selected.items(), key=lambda item: item[1][1].timestamp_sec):
        ui = detector.detect(packet.frame, frame_index=packet.frame_index, timestamp_sec=packet.timestamp_sec)
        crops = detector.crop_regions(packet.frame, ui)
        stem = _frame_stem(packet.frame_index, packet.timestamp_sec)
        visual_path = visual_dir / f"{stem}_respawn_{rt:.3f}.jpg"
        cv2.imwrite(str(visual_path), packet.frame)
        minimap_path = None
        location_path = None
        if "minimap" in crops:
            minimap_path = minimap_dir / f"{stem}_minimap.jpg"
            cv2.imwrite(str(minimap_path), crops["minimap"])
        if "location_text" in crops:
            location_path = location_dir / f"{stem}_location_text.jpg"
            cv2.imwrite(str(location_path), crops["location_text"])
        records.append({
            "spawn_name": spawn_name,
            "respawn_time": rt,
            "target_time": target_t,
            "frame_index": packet.frame_index,
            "timestamp_sec": packet.timestamp_sec,
            "visual_path": str(visual_path),
            "minimap_path": str(minimap_path) if minimap_path else None,
            "location_text_path": str(location_path) if location_path else None,
        })

    report = {
        "source": {"video": args.video, "respawn_report": args.respawn_report},
        "spawn_name": spawn_name,
        "num_saved_references": len(records),
        "records": records,
    }
    report_path = Path(args.out) / "spawn_reference_bootstrap_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--respawn-report", required=True)
    parser.add_argument("--out", required=True, help="spawn reference root directory")
    parser.add_argument("--spawn-name", default=None, help="e.g. 'BL Base'")
    parser.add_argument("--spawn-config", default=None)
    parser.add_argument("--offsets", default="0.0,0.4,0.8")
    parser.add_argument("--sample-fps", type=float, default=10.0)
    parser.add_argument("--resize", default="1920x1080")
    parser.add_argument("--base-resolution", default="1920x1080")
    parser.add_argument("--ui-templates", default=None)
    parser.add_argument("--max-frame-delta-sec", type=float, default=0.65)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    args = parser.parse_args()

    Path(args.out).mkdir(parents=True, exist_ok=True)
    report = collect_from_video(args)
    print(json.dumps({
        "report_path": str(Path(args.out) / "spawn_reference_bootstrap_report.json"),
        "spawn_name": report["spawn_name"],
        "num_saved_references": report["num_saved_references"],
        "out_dir": args.out,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
