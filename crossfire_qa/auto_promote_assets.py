from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


NOTIFICATION_CLASSES = {"kill_feed", "first_kill_medal", "death_killer_panel"}
DIGIT_CLASSES = {str(i) for i in range(10)}


@dataclass
class PromotionDecision:
    candidate_id: str
    class_name: str
    image_path: str
    decision: str
    confidence: float
    destination_path: Optional[str]
    reasons: list[str]
    metadata: dict[str, Any]


def _json_load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_dump(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def image_dhash(img: np.ndarray, hash_size: int = 8) -> str:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img.copy()
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return f"{value:0{hash_size * hash_size // 4}x}"


def hamming_hex(a: str, b: str) -> int:
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return 999


def image_quality(path: Path) -> dict[str, float]:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return {"available": 0.0, "blur": 0.0, "bright_ratio": 0.0, "dark_ratio": 1.0, "contrast": 0.0}
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return {
        "available": 1.0,
        "blur": blur,
        "bright_ratio": float(np.mean(gray > 175)),
        "dark_ratio": float(np.mean(gray < 55)),
        "contrast": float(np.std(gray) / 128.0),
    }


def kill_feed_foreground_metrics(path: Path) -> dict[str, float]:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return {"available": 0.0, "color_ratio": 0.0, "dark_ratio": 0.0, "edge_ratio": 0.0, "foreground_score": 0.0}
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    color_ratio = float(np.mean((hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 70) & (hsv[:, :, 2] < 245)))
    dark_ratio = float(np.mean(gray < 70))
    edge_ratio = float(np.mean(cv2.Canny(gray, 80, 160) > 0))
    foreground_score = clamp01(
        0.55 * clamp01(color_ratio / 0.03)
        + 0.25 * clamp01(dark_ratio / 0.08)
        + 0.20 * clamp01(edge_ratio / 0.04)
    )
    return {
        "available": 1.0,
        "color_ratio": color_ratio,
        "dark_ratio": dark_ratio,
        "edge_ratio": edge_ratio,
        "foreground_score": foreground_score,
    }


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def resolve_image_path(path_value: str, base_dirs: list[Path]) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    for base in base_dirs:
        candidate = base / p
        if candidate.exists():
            return candidate
    return p


def discover_temporal_representatives(args: argparse.Namespace) -> tuple[list[dict[str, Any]], Path]:
    temporal_dir = Path(args.temporal_assets).expanduser().resolve()
    reps_path = temporal_dir / "representatives.json"
    if not reps_path.exists():
        return [], temporal_dir
    data = _json_load(reps_path)
    if not isinstance(data, list):
        raise ValueError(f"Expected list in {reps_path}")
    return data, temporal_dir


def score_kill_feed(item: dict[str, Any], quality: dict[str, float], args: argparse.Namespace) -> tuple[float, list[str]]:
    metadata = item.get("metadata", {}) or {}
    actor = metadata.get("kill_feed_actor", {}) or {}
    row_score = float(actor.get("row_score", 0.0) or 0.0)
    score_event = metadata.get("score_event", {}) or {}
    hash_distance = float(score_event.get("hash_distance", item.get("score", 0.0)) or 0.0)
    trigger_bonus = 1.0 if item.get("trigger") == "score_change_window" else 0.4
    quality_score = clamp01(quality.get("blur", 0.0) / max(1.0, args.blur_good))
    row_component = clamp01(row_score / max(0.001, args.kill_feed_row_good))
    hash_component = clamp01(hash_distance / max(1.0, args.score_change_hash_good))
    confidence = clamp01(0.40 * row_component + 0.25 * hash_component + 0.20 * quality_score + 0.15 * trigger_bonus)

    reasons = [
        f"row_score={row_score:.3f}",
        f"score_hash_distance={hash_distance:.1f}",
        f"blur={quality.get('blur', 0.0):.1f}",
    ]
    if actor.get("actor_scope"):
        reasons.append(f"actor_scope={actor.get('actor_scope')}")
    return confidence, reasons


def kill_feed_event_key(item: dict[str, Any]) -> tuple[Any, ...]:
    metadata = item.get("metadata", {}) or {}
    event = metadata.get("score_event", {}) or {}
    return (
        event.get("video_id", item.get("source_video")),
        event.get("slot"),
        event.get("frame_index", item.get("trigger_time_sec")),
    )


def select_kill_feed_event_representatives(
    reps: list[dict[str, Any]],
    temporal_dir: Path,
    args: argparse.Namespace,
) -> set[str]:
    base_dirs = [Path.cwd(), temporal_dir, temporal_dir.parent]
    grouped: dict[tuple[Any, ...], list[tuple[float, str]]] = {}
    for item in reps:
        if str(item.get("class_name", "")) != "kill_feed":
            continue
        candidate_id = str(item.get("candidate_id", "candidate"))
        image_path = resolve_image_path(str(item.get("image_path", "")), base_dirs)
        metadata = item.get("metadata", {}) or {}
        actor = metadata.get("kill_feed_actor", {}) or {}
        row_score = float(actor.get("row_score", 0.0) or 0.0)
        foreground = kill_feed_foreground_metrics(image_path)
        trigger_time = float(item.get("trigger_time_sec", item.get("timestamp_sec", 0.0)) or 0.0)
        timestamp = float(item.get("timestamp_sec", trigger_time) or trigger_time)
        temporal_score = clamp01(1.0 - abs(timestamp - trigger_time) / max(0.001, args.kill_feed_event_window_sec))
        rank_score = (
            0.45 * clamp01(row_score / max(0.001, args.kill_feed_row_good))
            + 0.40 * foreground["foreground_score"]
            + 0.15 * temporal_score
        )
        grouped.setdefault(kill_feed_event_key(item), []).append((rank_score, candidate_id))

    selected: set[str] = set()
    for values in grouped.values():
        for _, candidate_id in sorted(values, reverse=True)[: args.kill_feed_max_per_score_event]:
            selected.add(candidate_id)
    return selected


def score_death_panel(item: dict[str, Any], quality: dict[str, float], args: argparse.Namespace) -> tuple[float, list[str]]:
    metadata = item.get("metadata", {}) or {}
    death_event = metadata.get("death_event", {}) or {}
    event_score = float(death_event.get("score", item.get("score", 0.0)) or 0.0)
    killer_cue = float(metadata.get("killer_cue_score", death_event.get("killer_cue_score", 0.0)) or 0.0)
    alive_hud = float(metadata.get("alive_hud_score", death_event.get("alive_hud_score", 1.0)) or 1.0)
    quality_score = clamp01(quality.get("blur", 0.0) / max(1.0, args.blur_good))
    confidence = clamp01(
        0.45 * clamp01(event_score)
        + 0.30 * clamp01(killer_cue)
        + 0.20 * clamp01(1.0 - alive_hud)
        + 0.05 * quality_score
    )
    reasons = [
        f"death_score={event_score:.3f}",
        f"killer_cue_score={killer_cue:.3f}",
        f"alive_hud_score={alive_hud:.3f}",
        f"blur={quality.get('blur', 0.0):.1f}",
    ]
    return confidence, reasons


def score_first_kill_medal(item: dict[str, Any], quality: dict[str, float], args: argparse.Namespace) -> tuple[float, list[str]]:
    metadata = item.get("metadata", {}) or {}
    score_event = metadata.get("score_event", {}) or {}
    hash_distance = float(score_event.get("hash_distance", item.get("score", 0.0)) or 0.0)
    quality_score = clamp01(quality.get("blur", 0.0) / max(1.0, args.blur_good))
    confidence = clamp01(0.45 * clamp01(hash_distance / max(1.0, args.score_change_hash_good)) + 0.35 * quality_score + 0.20)
    return confidence, [f"score_hash_distance={hash_distance:.1f}", f"blur={quality.get('blur', 0.0):.1f}", "optional_signal"]


def decision_threshold(class_name: str, args: argparse.Namespace) -> float:
    if class_name == "kill_feed":
        return args.kill_feed_promote_threshold
    if class_name == "death_killer_panel":
        return args.death_panel_promote_threshold
    if class_name == "first_kill_medal":
        return args.first_kill_promote_threshold
    return 1.1


def score_notification_candidate(item: dict[str, Any], image_path: Path, args: argparse.Namespace) -> tuple[float, list[str], dict[str, float]]:
    quality = image_quality(image_path)
    class_name = str(item.get("class_name", ""))
    if quality.get("available", 0.0) < 1.0:
        return 0.0, ["image_unavailable"], quality
    if quality.get("blur", 0.0) < args.blur_min:
        return 0.0, [f"blur_too_low:{quality.get('blur', 0.0):.1f}<{args.blur_min:.1f}"], quality
    if class_name == "kill_feed":
        confidence, reasons = score_kill_feed(item, quality, args)
    elif class_name == "death_killer_panel":
        confidence, reasons = score_death_panel(item, quality, args)
    elif class_name == "first_kill_medal":
        confidence, reasons = score_first_kill_medal(item, quality, args)
    else:
        confidence, reasons = 0.0, [f"unsupported_class:{class_name}"]
    return confidence, reasons, quality


def copy_promoted(src: Path, dst_root: Path, class_name: str, candidate_id: str) -> Path:
    dst_dir = dst_root / "notification_templates" / class_name
    dst_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix if src.suffix else ".jpg"
    dst = dst_dir / f"{candidate_id}{suffix}"
    shutil.copy2(src, dst)
    return dst


def promote_notifications(args: argparse.Namespace) -> list[PromotionDecision]:
    reps, temporal_dir = discover_temporal_representatives(args)
    out_dir = Path(args.out).expanduser().resolve()
    decisions: list[PromotionDecision] = []
    promoted_hashes_by_class: dict[str, list[str]] = {}
    base_dirs = [Path.cwd(), temporal_dir, temporal_dir.parent]
    selected_kill_feed_ids = select_kill_feed_event_representatives(reps, temporal_dir, args)

    for item in reps:
        class_name = str(item.get("class_name", ""))
        candidate_id = str(item.get("candidate_id", "candidate"))
        image_value = str(item.get("image_path", ""))
        image_path = resolve_image_path(image_value, base_dirs)
        metadata = dict(item.get("metadata", {}) or {})

        confidence, reasons, quality = score_notification_candidate(item, image_path, args)
        metadata["promotion_quality"] = quality
        if class_name == "kill_feed":
            foreground = kill_feed_foreground_metrics(image_path)
            metadata["promotion_kill_feed_foreground"] = foreground
            reasons.append(f"foreground_score={foreground['foreground_score']:.3f}")

        decision = "review"
        destination: Optional[Path] = None
        if class_name not in NOTIFICATION_CLASSES:
            reasons.append("not_a_notification_template_class")
        elif class_name == "kill_feed" and candidate_id not in selected_kill_feed_ids:
            reasons.append("not_selected_as_score_event_representative")
        elif (
            class_name == "kill_feed"
            and args.require_kill_feed_ocr_when_available
            and (metadata.get("kill_feed_actor", {}) or {}).get("ocr_available")
            and not (metadata.get("kill_feed_actor", {}) or {}).get("ocr_items")
        ):
            reasons.append("kill_feed_ocr_text_absent")
        elif class_name == "kill_feed" and metadata.get("promotion_kill_feed_foreground", {}).get("foreground_score", 0.0) < args.kill_feed_foreground_min:
            reasons.append(
                "kill_feed_foreground_too_low:"
                f"{metadata.get('promotion_kill_feed_foreground', {}).get('foreground_score', 0.0):.3f}"
                f"<{args.kill_feed_foreground_min:.3f}"
            )
        elif confidence >= decision_threshold(class_name, args):
            img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if img is None:
                reasons.append("image_read_failed")
            else:
                h = image_dhash(img)
                duplicate = any(hamming_hex(h, prev) <= args.dedupe_threshold for prev in promoted_hashes_by_class.get(class_name, []))
                if duplicate:
                    decision = "duplicate"
                    reasons.append("duplicate_of_already_promoted_template")
                else:
                    decision = "promoted"
                    promoted_hashes_by_class.setdefault(class_name, []).append(h)
                    destination = copy_promoted(image_path, out_dir, class_name, candidate_id)
        else:
            reasons.append(f"below_threshold:{confidence:.3f}<{decision_threshold(class_name, args):.3f}")

        decisions.append(
            PromotionDecision(
                candidate_id=candidate_id,
                class_name=class_name,
                image_path=str(image_path),
                decision=decision,
                confidence=confidence,
                destination_path=str(destination) if destination else None,
                reasons=reasons,
                metadata=metadata,
            )
        )
    return decisions


def copy_existing_digit_templates(args: argparse.Namespace) -> list[PromotionDecision]:
    bootstrap_dir = Path(args.bootstrap_assets).expanduser().resolve() if args.bootstrap_assets else None
    if bootstrap_dir is None:
        return []
    source_root = bootstrap_dir / "digit_templates"
    if not source_root.exists():
        return []
    out_root = Path(args.out).expanduser().resolve() / "digit_templates"
    decisions: list[PromotionDecision] = []
    for digit in sorted(DIGIT_CLASSES):
        d = source_root / digit
        if not d.exists():
            continue
        for src in sorted(p for p in d.iterdir() if p.is_file()):
            dst_dir = out_root / digit
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name
            shutil.copy2(src, dst)
            decisions.append(
                PromotionDecision(
                    candidate_id=src.stem,
                    class_name=f"digit_{digit}",
                    image_path=str(src),
                    decision="promoted_existing_labeled_template",
                    confidence=1.0,
                    destination_path=str(dst),
                    reasons=["existing_labeled_digit_template"],
                    metadata={},
                )
            )
    return decisions


def make_contact_sheet(decisions: list[PromotionDecision], out_path: Path, *, decision_filter: str, thumb_size: tuple[int, int] = (220, 110), cols: int = 4) -> None:
    selected = [d for d in decisions if d.decision == decision_filter and d.class_name in NOTIFICATION_CLASSES]
    if not selected:
        return
    thumbs = []
    for d in selected:
        img = cv2.imread(d.image_path, cv2.IMREAD_COLOR)
        if img is None:
            continue
        thumb = cv2.resize(img, thumb_size, interpolation=cv2.INTER_AREA)
        label = f"{d.class_name} {d.confidence:.2f}"
        cv2.putText(thumb, label[:34], (5, thumb_size[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
        thumbs.append(thumb)
    if not thumbs:
        return
    rows = int(np.ceil(len(thumbs) / cols))
    canvas = np.zeros((rows * thumb_size[1], cols * thumb_size[0], 3), dtype=np.uint8)
    for idx, thumb in enumerate(thumbs):
        y = (idx // cols) * thumb_size[1]
        x = (idx % cols) * thumb_size[0]
        canvas[y:y + thumb_size[1], x:x + thumb_size[0]] = thumb
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), canvas)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Conservatively auto-promote high-confidence non-VLM assets into template folders.")
    parser.add_argument("--temporal-assets", required=True, help="Directory containing temporal representatives.json.")
    parser.add_argument("--bootstrap-assets", default=None, help="Optional bootstrap_assets directory; existing labeled digit templates are copied.")
    parser.add_argument("--out", required=True, help="Promotion output root.")
    parser.add_argument("--kill-feed-promote-threshold", type=float, default=0.78)
    parser.add_argument("--death-panel-promote-threshold", type=float, default=0.78)
    parser.add_argument("--first-kill-promote-threshold", type=float, default=0.90)
    parser.add_argument("--kill-feed-row-good", type=float, default=0.50)
    parser.add_argument("--kill-feed-foreground-min", type=float, default=0.45)
    parser.add_argument("--kill-feed-max-per-score-event", type=int, default=2)
    parser.add_argument("--kill-feed-event-window-sec", type=float, default=1.2)
    parser.add_argument("--allow-kill-feed-without-ocr", dest="require_kill_feed_ocr_when_available", action="store_false")
    parser.set_defaults(require_kill_feed_ocr_when_available=True)
    parser.add_argument("--score-change-hash-good", type=float, default=20.0)
    parser.add_argument("--blur-min", type=float, default=10.0)
    parser.add_argument("--blur-good", type=float, default=80.0)
    parser.add_argument("--dedupe-threshold", type=int, default=8)
    args = parser.parse_args()
    return args


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    notification_decisions = promote_notifications(args)
    digit_decisions = copy_existing_digit_templates(args)
    decisions = notification_decisions + digit_decisions
    counts = Counter(d.decision for d in decisions)
    by_class = Counter(d.class_name for d in decisions if d.decision.startswith("promoted"))

    make_contact_sheet(notification_decisions, out_dir / "contact_sheet_promoted.jpg", decision_filter="promoted")
    make_contact_sheet(notification_decisions, out_dir / "contact_sheet_review.jpg", decision_filter="review")

    report = {
        "method": "conservative_non_vlm_auto_promotion",
        "description": (
            "Promotes only high-confidence temporal notification representatives using CV/OCR metadata, "
            "quality checks, temporal evidence, and dedupe. Low-confidence items remain review candidates."
        ),
        "inputs": {
            "temporal_assets": str(Path(args.temporal_assets).expanduser().resolve()),
            "bootstrap_assets": str(Path(args.bootstrap_assets).expanduser().resolve()) if args.bootstrap_assets else None,
        },
        "output_dir": str(out_dir),
        "thresholds": {
            "kill_feed_promote_threshold": args.kill_feed_promote_threshold,
            "death_panel_promote_threshold": args.death_panel_promote_threshold,
            "first_kill_promote_threshold": args.first_kill_promote_threshold,
            "blur_min": args.blur_min,
            "dedupe_threshold": args.dedupe_threshold,
        },
        "summary": {
            "num_decisions": len(decisions),
            "counts_by_decision": dict(counts),
            "promoted_by_class": dict(by_class),
        },
        "decisions": [asdict(d) for d in decisions],
    }
    _json_dump(report, out_dir / "auto_promotion_report.json")
    print(json.dumps({
        "report": str(out_dir / "auto_promotion_report.json"),
        "output_dir": str(out_dir),
        "summary": report["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
