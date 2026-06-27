"""
EasyOCR 있음
→ 자동 라벨링 template 생성

EasyOCR 없음
→ 후보 이미지 자동 수집

Manual label 있음
→ 정확한 template 생성

아무 라벨 없음
→ unlabeled_candidates 저장
"""


from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import cv2
import numpy as np

from kill_count_reader import (
    Box,
    OptionalEasyOCRReader,
    load_score_reader_config,
    preprocess_digit_image,
    segment_digit_candidates,
)
from setup_optional_deps import ensure_easyocr, is_import_available
from ui_detector import CrossFireUIDetector
from video_sampler import MP4FrameSampler, parse_resize


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


@dataclass
class ScoreCropPacket:
    top_score_crop_bgr: np.ndarray
    frame_index: int
    timestamp_sec: float
    crop_path: Optional[str]


@dataclass
class SavedTemplateRecord:
    digit: str
    path: str
    frame_index: int
    timestamp_sec: float
    slot: str
    side: str
    source: str
    confidence: float
    bbox_in_score_subcrop: list[int]
    hash: str


@dataclass
class UnlabeledCandidateRecord:
    path: str
    frame_index: int
    timestamp_sec: float
    slot: str
    side: str
    reason: str
    candidate_index: int
    bbox_in_score_subcrop: list[int]


@dataclass
class BootstrapReport:
    source: dict[str, Any]
    label_source: str
    score_sub_rois: dict[str, dict[str, int]]
    side_map: dict[str, str]
    saved_templates: list[SavedTemplateRecord] = field(default_factory=list)
    unlabeled_candidates: list[UnlabeledCandidateRecord] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)


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


def _frame_stem(frame_index: int, timestamp_sec: float) -> str:
    return f"frame_{frame_index:06d}_t{timestamp_sec:08.3f}"


def _resolve_path(path_value: str, base_dir: Path) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    if p.exists():
        return p
    return base_dir / p


def iter_top_score_crops_from_ui_report(ui_report_path: str | Path, include_non_gameplay: bool = False) -> Iterator[ScoreCropPacket]:
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
        p = _resolve_path(str(crop_path), base_dir)
        crop = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if crop is None:
            print(f"[WARN] failed to read crop: {p}", file=sys.stderr)
            continue
        if not include_non_gameplay and not is_gameplay_score_bar(crop):
            continue
        yield ScoreCropPacket(
            top_score_crop_bgr=crop,
            frame_index=int(det.get("frame_index", -1)),
            timestamp_sec=float(det.get("timestamp_sec", 0.0)),
            crop_path=str(p),
        )


def iter_top_score_crops_from_video(args: argparse.Namespace) -> Iterator[ScoreCropPacket]:
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

    with MP4FrameSampler(
        video_path=args.video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for packet in sampler.iter_frames():
            result = detector.detect(packet.frame, frame_index=packet.frame_index, timestamp_sec=packet.timestamp_sec)
            crops = detector.crop_regions(packet.frame, result)
            if not args.include_non_gameplay and not is_gameplay_score_bar(crops["top_score_bar"]):
                continue
            yield ScoreCropPacket(
                top_score_crop_bgr=crops["top_score_bar"],
                frame_index=packet.frame_index,
                timestamp_sec=packet.timestamp_sec,
                crop_path=None,
            )


def load_manual_labels(path: Optional[str | Path]) -> dict[tuple[int, str], str]:
    """
    Load manual labels.

    Supported JSON shapes:
      {"labels": [{"frame_index": 1800, "slot": "left_score", "value": "1"}]}
      {"labels": [{"frame_index": 1800, "side": "GR", "value": "1"}]}
      [{"frame_index": 1800, "slot": "left_score", "value": "1"}]

    Key uses (frame_index, slot_or_side). Timestamp-based matching is intentionally
    avoided for template collection because frame_index is more stable.
    """
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("labels", [])
    labels: dict[tuple[int, str], str] = {}
    for item in items:
        frame_index = int(item["frame_index"])
        key_name = str(item.get("slot") or item.get("side"))
        value = re.sub(r"\D", "", str(item.get("value", "")))
        if key_name and value:
            labels[(frame_index, key_name)] = value
    return labels


def split_score_slots(top_score_crop_bgr: np.ndarray, score_sub_rois: dict[str, dict[str, int]]) -> dict[str, np.ndarray]:
    h, w = top_score_crop_bgr.shape[:2]
    out: dict[str, np.ndarray] = {}
    for slot, cfg in score_sub_rois.items():
        box = Box(int(cfg["x"]), int(cfg["y"]), int(cfg["w"]), int(cfg["h"])).clip(w, h)
        out[slot] = top_score_crop_bgr[box.y:box.y2, box.x:box.x2]
    return out


def image_hash(img: np.ndarray) -> str:
    normalized = preprocess_digit_image(img, output_size=(24, 32))
    return hashlib.sha1(normalized.tobytes()).hexdigest()[:16]


def save_digit_template(
    digit_img_gray: np.ndarray,
    output_dir: Path,
    digit: str,
    *,
    frame_index: int,
    timestamp_sec: float,
    slot: str,
    side: str,
    candidate_index: int,
    source: str,
    confidence: float,
    bbox: list[int],
    seen_hashes: set[str],
) -> Optional[SavedTemplateRecord]:
    h = image_hash(digit_img_gray)
    if h in seen_hashes:
        return None
    seen_hashes.add(h)

    digit_dir = output_dir / digit
    digit_dir.mkdir(parents=True, exist_ok=True)
    name = f"{digit}_{_frame_stem(frame_index, timestamp_sec)}_{slot}_c{candidate_index:02d}_{h}.png"
    path = digit_dir / name
    cv2.imwrite(str(path), digit_img_gray)
    return SavedTemplateRecord(
        digit=digit,
        path=str(path),
        frame_index=frame_index,
        timestamp_sec=timestamp_sec,
        slot=slot,
        side=side,
        source=source,
        confidence=confidence,
        bbox_in_score_subcrop=bbox,
        hash=h,
    )


def save_unlabeled_candidate(
    digit_img_gray: np.ndarray,
    output_dir: Path,
    *,
    frame_index: int,
    timestamp_sec: float,
    slot: str,
    side: str,
    candidate_index: int,
    reason: str,
    bbox: list[int],
) -> UnlabeledCandidateRecord:
    d = output_dir / "unlabeled_candidates"
    d.mkdir(parents=True, exist_ok=True)
    h = image_hash(digit_img_gray)
    path = d / f"candidate_{_frame_stem(frame_index, timestamp_sec)}_{slot}_c{candidate_index:02d}_{h}.png"
    cv2.imwrite(str(path), digit_img_gray)
    return UnlabeledCandidateRecord(
        path=str(path),
        frame_index=frame_index,
        timestamp_sec=timestamp_sec,
        slot=slot,
        side=side,
        reason=reason,
        candidate_index=candidate_index,
        bbox_in_score_subcrop=bbox,
    )


def make_contact_sheet(image_paths: list[str], output_path: str | Path, thumb_size: tuple[int, int] = (48, 64), cols: int = 12) -> None:
    if not image_paths:
        return
    thumbs: list[np.ndarray] = []
    for p in image_paths:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        img = cv2.resize(img, thumb_size, interpolation=cv2.INTER_AREA)
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        thumbs.append(img)
    if not thumbs:
        return
    rows = int(np.ceil(len(thumbs) / cols))
    sheet = np.full((rows * thumb_size[1], cols * thumb_size[0], 3), 255, np.uint8)
    for i, img in enumerate(thumbs):
        r, c = divmod(i, cols)
        y, x = r * thumb_size[1], c * thumb_size[0]
        sheet[y:y + thumb_size[1], x:x + thumb_size[0]] = img
    cv2.imwrite(str(output_path), sheet)


def read_easyocr_label(ocr: OptionalEasyOCRReader, crop_bgr: np.ndarray) -> tuple[str, float, str]:
    reading = ocr.read_number(crop_bgr)
    if reading.value is None:
        return "", reading.confidence, reading.status
    return re.sub(r"\D", "", reading.raw_text or str(reading.value)), reading.confidence, reading.status


def bootstrap_templates(args: argparse.Namespace) -> BootstrapReport:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.install_easyocr:
        status = ensure_easyocr(user=args.user_install, upgrade=args.upgrade_install, dry_run=False)
        if not status.available_after:
            print(f"[WARN] easyocr installation failed: {status.error}", file=sys.stderr)

    score_sub_rois, side_map = load_score_reader_config(args.score_config)
    manual_labels = load_manual_labels(args.manual_labels)

    ocr: Optional[OptionalEasyOCRReader] = None
    if args.label_source == "easyocr":
        if not is_import_available("easyocr"):
            raise RuntimeError("easyocr is not installed. Run setup_optional_deps.py --easyocr or use --install-easyocr.")
        ocr = OptionalEasyOCRReader()
        if not ocr.available:
            raise RuntimeError(f"easyocr unavailable: {ocr.error}")

    if args.video:
        packets = iter_top_score_crops_from_video(args)
        source = {"type": "video", "path": args.video, "sample_fps": args.sample_fps, "resize": args.resize}
    else:
        packets = iter_top_score_crops_from_ui_report(args.ui_report, include_non_gameplay=args.include_non_gameplay)
        source = {"type": "ui_report", "path": args.ui_report}

    report = BootstrapReport(
        source=source,
        label_source=args.label_source,
        score_sub_rois=score_sub_rois,
        side_map=side_map,
    )
    seen_hashes: set[str] = set()

    for packet_i, packet in enumerate(packets):
        if args.max_frames is not None and packet_i >= args.max_frames:
            break

        slots = split_score_slots(packet.top_score_crop_bgr, score_sub_rois)
        for slot, score_crop in slots.items():
            side = side_map.get(slot, slot)
            segments = segment_digit_candidates(score_crop)
            if not segments:
                report.skipped.append({
                    "frame_index": packet.frame_index,
                    "timestamp_sec": packet.timestamp_sec,
                    "slot": slot,
                    "side": side,
                    "reason": "no_digit_candidate",
                })
                continue

            label_digits = ""
            label_conf = 0.0
            label_status = ""
            label_source = args.label_source

            if args.label_source == "manual-json":
                label_digits = manual_labels.get((packet.frame_index, slot), "") or manual_labels.get((packet.frame_index, side), "")
                label_conf = 1.0 if label_digits else 0.0
                label_status = "ok" if label_digits else "manual_label_missing"
            elif args.label_source == "easyocr":
                assert ocr is not None
                label_digits, label_conf, label_status = read_easyocr_label(ocr, score_crop)
            elif args.label_source == "none":
                label_status = "unlabeled_mode"
            else:  # pragma: no cover
                raise ValueError(args.label_source)

            if label_digits and len(label_digits) == len(segments) and label_conf >= args.min_label_confidence:
                for ci, ((box, digit_img), digit) in enumerate(zip(segments, label_digits)):
                    rec = save_digit_template(
                        digit_img,
                        out_dir,
                        digit,
                        frame_index=packet.frame_index,
                        timestamp_sec=packet.timestamp_sec,
                        slot=slot,
                        side=side,
                        candidate_index=ci,
                        source=label_source,
                        confidence=float(label_conf),
                        bbox=box.to_list(),
                        seen_hashes=seen_hashes,
                    )
                    if rec is not None:
                        report.saved_templates.append(rec)
                continue

            reason = (
                f"label_status={label_status}; label_digits={label_digits!r}; "
                f"segments={len(segments)}; conf={label_conf:.3f}"
            )
            if args.save_unlabeled or args.label_source == "none":
                for ci, (box, digit_img) in enumerate(segments):
                    report.unlabeled_candidates.append(
                        save_unlabeled_candidate(
                            digit_img,
                            out_dir,
                            frame_index=packet.frame_index,
                            timestamp_sec=packet.timestamp_sec,
                            slot=slot,
                            side=side,
                            candidate_index=ci,
                            reason=reason,
                            bbox=box.to_list(),
                        )
                    )
            else:
                report.skipped.append({
                    "frame_index": packet.frame_index,
                    "timestamp_sec": packet.timestamp_sec,
                    "slot": slot,
                    "side": side,
                    "reason": reason,
                })

    return report


def write_report(report: BootstrapReport, out_dir: Path) -> None:
    report_path = out_dir / "digit_template_bootstrap_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(report), f, ensure_ascii=False, indent=2)

    csv_path = out_dir / "digit_template_bootstrap_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["digit", "path", "frame_index", "timestamp_sec", "slot", "side", "source", "confidence", "hash"],
        )
        writer.writeheader()
        for r in report.saved_templates:
            writer.writerow({
                "digit": r.digit,
                "path": r.path,
                "frame_index": r.frame_index,
                "timestamp_sec": r.timestamp_sec,
                "slot": r.slot,
                "side": r.side,
                "source": r.source,
                "confidence": r.confidence,
                "hash": r.hash,
            })

    # One contact sheet per digit plus one for unlabeled candidates.
    for digit in map(str, range(10)):
        paths = [r.path for r in report.saved_templates if r.digit == digit]
        make_contact_sheet(paths, out_dir / f"contact_sheet_digit_{digit}.jpg")
    make_contact_sheet([r.path for r in report.unlabeled_candidates], out_dir / "contact_sheet_unlabeled.jpg")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap CrossFire score digit templates from top_score_bar crops.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=str, help="input mp4 path; internally calls sampler + UI detector")
    src.add_argument("--ui-report", type=str, help="ui_detection_report.json generated with --save-crops")

    parser.add_argument("--out", type=str, required=True, help="output digit template directory")
    parser.add_argument("--score-config", type=str, default=None)
    parser.add_argument("--label-source", choices=["easyocr", "manual-json", "none"], default="easyocr")
    parser.add_argument("--manual-labels", type=str, default=None, help="required when --label-source manual-json")
    parser.add_argument("--install-easyocr", action="store_true", help="attempt pip install easyocr before bootstrapping")
    parser.add_argument("--user-install", action="store_true", help="use pip install --user when --install-easyocr is enabled")
    parser.add_argument("--upgrade-install", action="store_true", help="use pip install --upgrade when --install-easyocr is enabled")
    parser.add_argument("--min-label-confidence", type=float, default=0.60)
    parser.add_argument("--save-unlabeled", action="store_true", help="save candidates when automatic labeling fails")

    parser.add_argument("--sample-fps", type=float, default=5.0, help="only used with --video")
    parser.add_argument("--resize", type=str, default="1920x1080", help="only used with --video")
    parser.add_argument("--base-resolution", type=str, default="1920x1080")
    parser.add_argument("--roi-config", type=str, default=None)
    parser.add_argument("--ui-templates", type=str, default=None)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--include-non-gameplay", action="store_true", help="do not filter menu/loading frames")
    parser.add_argument("--max-frames", type=int, default=None)
    args = parser.parse_args()

    if args.label_source == "manual-json" and not args.manual_labels:
        parser.error("--manual-labels is required when --label-source manual-json")

    report = bootstrap_templates(args)
    out_dir = Path(args.out)
    write_report(report, out_dir)

    counts = {str(d): 0 for d in range(10)}
    for r in report.saved_templates:
        counts[r.digit] = counts.get(r.digit, 0) + 1

    print(json.dumps({
        "template_dir": str(out_dir),
        "saved_templates": len(report.saved_templates),
        "saved_by_digit": counts,
        "unlabeled_candidates": len(report.unlabeled_candidates),
        "report_path": str(out_dir / "digit_template_bootstrap_report.json"),
        "csv_path": str(out_dir / "digit_template_bootstrap_report.csv"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
