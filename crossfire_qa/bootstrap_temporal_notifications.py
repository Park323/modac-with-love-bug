from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


ROOT_DIR = Path(__file__).resolve().parent
DETECTOR_DIR = ROOT_DIR / "detector_layer"
CONFIG_DIR = ROOT_DIR / "configs"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

sys.path.insert(0, str(DETECTOR_DIR))
from bootstrap_notification_templates import is_gameplay_score_bar  # noqa: E402
from kill_count_reader import Box, load_score_reader_config  # noqa: E402
from notification_detector import NotificationDetector  # noqa: E402
from setup_optional_deps import ensure_easyocr, is_import_available  # noqa: E402
from ui_detector import CrossFireUIDetector  # noqa: E402
from video_sampler import MP4FrameSampler, parse_resize  # noqa: E402


NOTIFICATION_ROIS = {
    "kill_feed_area": "kill_feed",
    "kill_medal_area": "first_kill_medal",
    "death_killer_panel": "death_killer_panel",
}


@dataclass
class FrameObservation:
    source_video: str
    video_id: str
    frame_index: int
    timestamp_sec: float
    score_hashes: dict[str, str]
    death_panel_score: float
    killer_cue_score: float
    alive_hud_score: float
    death_panel_features: dict[str, float]
    killer_cue_features: dict[str, float]
    alive_hud_features: dict[str, dict[str, float]]
    crops: dict[str, np.ndarray]


@dataclass
class TemporalCandidate:
    candidate_id: str
    class_name: str
    roi_name: str
    source_video: str
    frame_index: int
    timestamp_sec: float
    image_path: str
    trigger: str
    trigger_time_sec: Optional[float]
    score: float
    metadata: dict[str, Any]


class OptionalEasyOCR:
    def __init__(self, enabled: bool, model_dir: Optional[str | Path] = None) -> None:
        self.enabled = enabled
        self.reader = None
        self.error = ""
        if not enabled:
            return
        try:
            import easyocr  # type: ignore

            kwargs: dict[str, Any] = {}
            if model_dir:
                model_path = Path(model_dir)
                model_path.mkdir(parents=True, exist_ok=True)
                kwargs["model_storage_directory"] = str(model_path)
                kwargs["user_network_directory"] = str(model_path)
            self.reader = easyocr.Reader(["en"], gpu=False, verbose=False, **kwargs)
        except Exception as exc:
            self.error = str(exc)

    @property
    def available(self) -> bool:
        return self.reader is not None

    def read(self, crop_bgr: np.ndarray) -> list[dict[str, Any]]:
        if self.reader is None:
            return []
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        results = self.reader.readtext(rgb, detail=1, paragraph=False)
        out: list[dict[str, Any]] = []
        for bbox, text, confidence in results:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
            out.append(
                {
                    "text": str(text),
                    "confidence": float(confidence),
                    "bbox": [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)],
                }
            )
        return out


def preprocess_for_ocr(crop_bgr: np.ndarray, scale: int = 3) -> np.ndarray:
    if crop_bgr.size == 0:
        return crop_bgr
    scale = max(1, int(scale))
    up = cv2.resize(
        crop_bgr,
        (crop_bgr.shape[1] * scale, crop_bgr.shape[0] * scale),
        interpolation=cv2.INTER_CUBIC,
    )
    gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.0)
    sharp = cv2.addWeighted(gray, 1.65, blurred, -0.65, 0)
    sharp = cv2.bilateralFilter(sharp, 5, 45, 45)
    return cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)


def rescale_ocr_items(items: list[dict[str, Any]], scale: float, variant: str) -> list[dict[str, Any]]:
    if scale <= 0:
        scale = 1.0
    out: list[dict[str, Any]] = []
    for item in items:
        x, y, w, h = item["bbox"]
        cloned = dict(item)
        cloned["bbox"] = [float(x) / scale, float(y) / scale, float(w) / scale, float(h) / scale]
        cloned["variant"] = variant
        out.append(cloned)
    return out


def _default_config(name: str) -> str | None:
    path = CONFIG_DIR / name
    return str(path) if path.exists() else None


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip())
    return safe or "item"


def _json_dump(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_player_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(ch.lower() for ch in value if ch.isalnum())


def discover_videos(dataset: Path) -> list[Path]:
    if dataset.is_file():
        if dataset.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Unsupported video extension: {dataset}")
        return [dataset.resolve()]
    if not dataset.exists():
        raise FileNotFoundError(f"Dataset path not found: {dataset}")
    return sorted(p.resolve() for p in dataset.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)


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


def build_detector(args: argparse.Namespace) -> CrossFireUIDetector:
    if args.roi_config:
        return CrossFireUIDetector.from_json(
            args.roi_config,
            template_dir=args.ui_templates,
            normalize_to_base=not args.no_normalize,
            apply_anchor_correction=not args.no_anchor_correction,
        )
    base_resolution = parse_resize(args.base_resolution)
    assert base_resolution is not None
    return CrossFireUIDetector(
        base_resolution=base_resolution,
        template_dir=args.ui_templates,
        normalize_to_base=not args.no_normalize,
        apply_anchor_correction=not args.no_anchor_correction,
    )


def death_panel_score(crop: np.ndarray) -> tuple[float, dict[str, float]]:
    f = NotificationDetector.extract_features(crop)
    has_detail = f.text_like_density > 0.010 or f.edge_density > 0.015 or f.bright_ratio > 0.010
    dark_cue = f.dark_ratio if has_detail else 0.0
    score = (
        0.35 * dark_cue
        + 2.8 * f.text_like_density
        + 1.8 * f.edge_density
        + 0.8 * f.bright_ratio
        + 0.35 * min(1.0, f.contrast)
    )
    return min(1.0, float(score)), {
        "bright_ratio": f.bright_ratio,
        "dark_ratio": f.dark_ratio,
        "edge_density": f.edge_density,
        "saturation_ratio": f.saturation_ratio,
        "text_like_density": f.text_like_density,
        "contrast": f.contrast,
    }


def _features_dict(crop: np.ndarray) -> tuple[Any, dict[str, float]]:
    f = NotificationDetector.extract_features(crop)
    return f, {
        "bright_ratio": f.bright_ratio,
        "dark_ratio": f.dark_ratio,
        "edge_density": f.edge_density,
        "saturation_ratio": f.saturation_ratio,
        "text_like_density": f.text_like_density,
        "contrast": f.contrast,
    }


def killer_cue_score(crop: np.ndarray) -> tuple[float, dict[str, float]]:
    f, features = _features_dict(crop)
    score = 5.0 * f.text_like_density + 0.8 * f.edge_density + 0.5 * min(1.0, f.contrast)
    return min(1.0, float(score)), features


def hud_presence_score(crop: np.ndarray) -> tuple[float, dict[str, float]]:
    f, features = _features_dict(crop)
    score = 4.0 * f.text_like_density + 1.2 * f.edge_density + f.bright_ratio
    return min(1.0, float(score)), features


def death_state_score(
    death_panel_crop: np.ndarray,
    killer_cue_crop: np.ndarray,
    hp_crop: np.ndarray,
    weapon_crop: np.ndarray,
) -> tuple[float, dict[str, float], float, dict[str, float], float, dict[str, dict[str, float]]]:
    panel_score, panel_features = death_panel_score(death_panel_crop)
    cue_score, cue_features = killer_cue_score(killer_cue_crop)
    hp_score, hp_features = hud_presence_score(hp_crop)
    weapon_score, weapon_features = hud_presence_score(weapon_crop)
    alive_score = float(np.mean([hp_score, weapon_score]))

    # A true death panel should appear with the central KILLER cue and without
    # strong normal-player HUD. This separates "I died" from "I killed someone".
    combined = 0.30 * panel_score + 0.50 * cue_score + 0.20 * (1.0 - alive_score)
    return (
        min(1.0, float(combined)),
        panel_features,
        cue_score,
        cue_features,
        alive_score,
        {"hp_ac_area": hp_features, "weapon_ammo_area": weapon_features},
    )


def parse_box(value: str) -> Box:
    parts = [int(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Expected x,y,w,h box, got: {value}")
    return Box(parts[0], parts[1], parts[2], parts[3])


def find_kill_feed_row(crop: np.ndarray) -> tuple[Box, float]:
    h, w = crop.shape[:2]
    if h == 0 or w == 0:
        return Box(0, 0, 1, 1), 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = ((gray > 125) | ((hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 80))).astype(np.uint8)
    energy = np.mean(mask, axis=1)
    if float(np.max(energy)) < 0.015:
        return Box(0, 0, w, min(h, 32)), 0.0

    threshold = max(0.015, float(np.max(energy)) * 0.35)
    active = np.where(energy >= threshold)[0]
    if len(active) == 0:
        return Box(0, 0, w, min(h, 32)), 0.0

    bands: list[tuple[int, int]] = []
    start = int(active[0])
    prev = int(active[0])
    for y in active[1:]:
        y = int(y)
        if y - prev > 3:
            bands.append((start, prev))
            start = y
        prev = y
    bands.append((start, prev))
    best = max(bands, key=lambda b: (b[1] - b[0] + 1) * float(np.mean(energy[b[0]:b[1] + 1])))
    y1 = max(0, best[0] - 6)
    y2 = min(h, best[1] + 7)

    row = mask[y1:y2, :]
    col_energy = np.mean(row, axis=0)
    cols = np.where(col_energy >= max(0.01, float(np.max(col_energy)) * 0.20))[0]
    if len(cols) == 0:
        x1, x2 = 0, w
    else:
        x1 = max(0, int(cols[0]) - 8)
        x2 = min(w, int(cols[-1]) + 9)
    score = float(np.mean(mask[y1:y2, x1:x2])) if x2 > x1 and y2 > y1 else 0.0
    return Box(x1, y1, max(1, x2 - x1), max(1, y2 - y1)), score


def save_debug_crop(path: Path, crop: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), crop)
    return str(path)


def classify_kill_actor(killer_name: str, victim_name: str, local_player_name: Optional[str]) -> tuple[str, list[str]]:
    local = normalize_player_name(local_player_name)
    killer = normalize_player_name(killer_name)
    victim = normalize_player_name(victim_name)
    notes: list[str] = []
    if not local:
        return "team_or_global_kill_unresolved", ["local_player_name_not_provided"]
    if killer and killer == local:
        return "own_kill", ["killer_name_matches_local_player"]
    if victim and victim == local:
        return "own_death_from_kill_feed", ["victim_name_matches_local_player"]
    if killer or victim:
        return "other_player_kill", ["killer/victim names parsed but local_player_name did not match"]
    notes.append("names_not_parsed; cannot resolve actor ownership")
    return "team_or_global_kill_unresolved", notes


def parse_kill_feed_actor(
    obs: FrameObservation,
    out_dir: Path,
    args: argparse.Namespace,
    ocr: OptionalEasyOCR,
) -> dict[str, Any]:
    crop = obs.crops["kill_feed_area"]
    row_box, row_score = find_kill_feed_row(crop)
    row_crop = crop[row_box.y:row_box.y2, row_box.x:row_box.x2]
    stem = f"{obs.video_id}_{obs.frame_index:06d}_{obs.timestamp_sec:08.3f}"
    row_path = save_debug_crop(out_dir / "candidates" / "kill_feed_rows" / f"{stem}_row.jpg", row_crop)
    processed_row_path: Optional[str] = None
    processed_row_crop: Optional[np.ndarray] = None
    if args.ocr_preprocess:
        processed_row_crop = preprocess_for_ocr(row_crop, scale=args.ocr_scale)
        processed_row_path = save_debug_crop(
            out_dir / "candidates" / "kill_feed_rows" / f"{stem}_row_ocr_preprocessed.jpg",
            processed_row_crop,
        )

    h, w = row_crop.shape[:2]
    left_crop = row_crop[:, : max(1, int(w * 0.46))]
    right_crop = row_crop[:, min(w - 1, int(w * 0.62)):]
    left_path = save_debug_crop(out_dir / "candidates" / "kill_feed_name_crops" / f"{stem}_killer_side.jpg", left_crop)
    right_path = save_debug_crop(out_dir / "candidates" / "kill_feed_name_crops" / f"{stem}_victim_side.jpg", right_crop)

    ocr_items: list[dict[str, Any]] = []
    killer_parts: list[str] = []
    victim_parts: list[str] = []
    if args.use_easyocr and ocr.available:
        ocr_items = rescale_ocr_items(ocr.read(row_crop), 1.0, "original")
        if processed_row_crop is not None:
            processed_items = ocr.read(processed_row_crop)
            ocr_items.extend(rescale_ocr_items(processed_items, float(args.ocr_scale), "preprocessed"))
        for item in ocr_items:
            x, _, bw, _ = item["bbox"]
            cx = float(x) + float(bw) / 2.0
            text = str(item["text"]).strip()
            if float(item.get("confidence", 0.0)) < args.ocr_min_confidence:
                continue
            if not text:
                continue
            if cx < w * 0.46:
                killer_parts.append(text)
            elif cx > w * 0.62:
                victim_parts.append(text)

    killer_name = " ".join(killer_parts).strip()
    victim_name = " ".join(victim_parts).strip()
    actor_scope, actor_notes = classify_kill_actor(killer_name, victim_name, args.local_player_name)
    if args.use_easyocr and not ocr.available:
        actor_notes.append(f"easyocr_unavailable:{ocr.error}")

    return {
        "row_bbox_in_kill_feed_area": row_box.to_list(),
        "row_score": row_score,
        "row_image_path": row_path,
        "preprocessed_row_image_path": processed_row_path,
        "killer_name_crop_path": left_path,
        "victim_name_crop_path": right_path,
        "ocr_enabled": bool(args.use_easyocr),
        "ocr_preprocess": bool(args.ocr_preprocess),
        "ocr_scale": int(args.ocr_scale),
        "ocr_available": bool(ocr.available),
        "ocr_items": ocr_items,
        "killer_name": killer_name or None,
        "victim_name": victim_name or None,
        "actor_scope": actor_scope,
        "actor_notes": actor_notes,
    }


def collect_observations(video: Path, args: argparse.Namespace) -> list[FrameObservation]:
    detector = build_detector(args)
    score_sub_rois, _ = load_score_reader_config(args.score_config)
    observations: list[FrameObservation] = []
    video_id = _safe_name(video.stem)

    with MP4FrameSampler(
        video_path=video,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize) if args.resize else None,
        color_format="bgr",
    ) as sampler:
        for i, packet in enumerate(sampler.iter_frames()):
            if args.max_frames is not None and i >= args.max_frames:
                break
            ui = detector.detect(packet.frame, frame_index=packet.frame_index, timestamp_sec=packet.timestamp_sec)
            all_crops = detector.crop_regions(packet.frame, ui)
            top_score = all_crops.get("top_score_bar")
            if top_score is None:
                continue
            if not args.include_non_gameplay and not is_gameplay_score_bar(top_score):
                continue

            score_hashes: dict[str, str] = {}
            h, w = top_score.shape[:2]
            for slot, cfg in score_sub_rois.items():
                box = Box(int(cfg["x"]), int(cfg["y"]), int(cfg["w"]), int(cfg["h"])).clip(w, h)
                score_hashes[slot] = image_dhash(top_score[box.y:box.y2, box.x:box.x2])

            crops = {name: all_crops[name] for name in NOTIFICATION_ROIS if name in all_crops}
            for aux_roi in ["hp_ac_area", "weapon_ammo_area"]:
                if aux_roi in all_crops:
                    crops[aux_roi] = all_crops[aux_roi]
            frame_h, frame_w = packet.frame.shape[:2]
            killer_box = parse_box(args.killer_cue_box).clip(frame_w, frame_h)
            killer_crop = packet.frame[killer_box.y:killer_box.y2, killer_box.x:killer_box.x2]
            crops["killer_cue_area"] = killer_crop

            panel_score, panel_features, cue_score, cue_features, alive_score, alive_features = death_state_score(
                crops["death_killer_panel"],
                killer_crop,
                crops["hp_ac_area"],
                crops["weapon_ammo_area"],
            )
            observations.append(
                FrameObservation(
                    source_video=str(video),
                    video_id=video_id,
                    frame_index=packet.frame_index,
                    timestamp_sec=packet.timestamp_sec,
                    score_hashes=score_hashes,
                    death_panel_score=panel_score,
                    killer_cue_score=cue_score,
                    alive_hud_score=alive_score,
                    death_panel_features=panel_features,
                    killer_cue_features=cue_features,
                    alive_hud_features=alive_features,
                    crops=crops,
                )
            )
    return observations


def detect_score_change_events(observations: list[FrameObservation], args: argparse.Namespace) -> list[dict[str, Any]]:
    last_hash_by_slot: dict[str, str] = {}
    last_event_time_by_slot: dict[str, float] = {}
    events: list[dict[str, Any]] = []

    for obs in observations:
        for slot, current_hash in obs.score_hashes.items():
            previous = last_hash_by_slot.get(slot)
            last_hash_by_slot[slot] = current_hash
            if previous is None:
                continue
            dist = hamming_hex(previous, current_hash)
            if dist < args.score_change_hash_threshold:
                continue
            last_t = last_event_time_by_slot.get(slot, -math.inf)
            if obs.timestamp_sec - last_t < args.min_event_gap_sec:
                continue
            last_event_time_by_slot[slot] = obs.timestamp_sec
            events.append(
                {
                    "event": "score_visual_change",
                    "source_video": obs.source_video,
                    "video_id": obs.video_id,
                    "slot": slot,
                    "time_sec": obs.timestamp_sec,
                    "frame_index": obs.frame_index,
                    "hash_distance": dist,
                    "status": "candidate",
                    "notes": ["visual score slot changed; inspect nearby kill_feed/medal crops"],
                }
            )
    return events


def group_death_panel_peaks(observations: list[FrameObservation], args: argparse.Namespace) -> list[dict[str, Any]]:
    high = [obs for obs in observations if is_death_observation(obs, args)]
    groups: list[list[FrameObservation]] = []
    for obs in high:
        if not groups or obs.timestamp_sec - groups[-1][-1].timestamp_sec > args.death_merge_gap_sec:
            groups.append([obs])
        else:
            groups[-1].append(obs)

    events: list[dict[str, Any]] = []
    for group in groups:
        peak = max(group, key=lambda x: x.death_panel_score)
        events.append(
            {
                "event": "death_panel_visual_peak",
                "source_video": peak.source_video,
                "video_id": peak.video_id,
                "time_sec": peak.timestamp_sec,
                "frame_index": peak.frame_index,
                "score": peak.death_panel_score,
                "killer_cue_score": peak.killer_cue_score,
                "alive_hud_score": peak.alive_hud_score,
                "start_time_sec": group[0].timestamp_sec,
                "end_time_sec": group[-1].timestamp_sec,
                "frame_count": len(group),
                "features": peak.death_panel_features,
                "killer_cue_features": peak.killer_cue_features,
                "alive_hud_features": peak.alive_hud_features,
                "status": "candidate",
                "notes": ["death evidence combines right Information panel, central KILLER cue, and weak alive HUD"],
            }
        )
    return events


def is_death_observation(obs: FrameObservation, args: argparse.Namespace) -> bool:
    return (
        obs.death_panel_score >= args.death_panel_min_score
        and obs.killer_cue_score >= args.killer_cue_min_score
        and obs.alive_hud_score <= args.alive_hud_max_score
    )


def save_candidate(
    obs: FrameObservation,
    out_dir: Path,
    class_name: str,
    roi_name: str,
    trigger: str,
    trigger_time_sec: Optional[float],
    score: float,
    metadata: dict[str, Any],
) -> TemporalCandidate:
    candidate_id = f"{obs.video_id}_{class_name}_{obs.frame_index:06d}_{trigger}"
    path = out_dir / "candidates" / class_name / f"{candidate_id}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), obs.crops[roi_name])
    return TemporalCandidate(
        candidate_id=candidate_id,
        class_name=class_name,
        roi_name=roi_name,
        source_video=obs.source_video,
        frame_index=obs.frame_index,
        timestamp_sec=obs.timestamp_sec,
        image_path=str(path),
        trigger=trigger,
        trigger_time_sec=trigger_time_sec,
        score=score,
        metadata=metadata,
    )


def mine_candidates(
    observations: list[FrameObservation],
    score_events: list[dict[str, Any]],
    death_events: list[dict[str, Any]],
    out_dir: Path,
    args: argparse.Namespace,
    ocr: OptionalEasyOCR,
) -> list[TemporalCandidate]:
    candidates: list[TemporalCandidate] = []

    for event in score_events:
        t = float(event["time_sec"])
        in_window = [
            obs for obs in observations
            if obs.video_id == event["video_id"]
            and t - args.kill_pre_sec <= obs.timestamp_sec <= t + args.kill_post_sec
        ]
        if args.max_frames_per_score_event:
            in_window = in_window[: args.max_frames_per_score_event]
        for obs in in_window:
            actor_info = parse_kill_feed_actor(obs, out_dir, args, ocr)
            actor_scope = str(actor_info.get("actor_scope") or "team_or_global_kill_unresolved")
            actor_notes = [
                "kill_feed can include kills by teammates/enemies; actor ownership requires reading killer/victim names"
            ]
            if args.local_player_name:
                actor_notes.append("compare OCR-extracted killer_name/victim_name with local_player_name before marking own_kill")
            candidates.append(
                save_candidate(
                    obs,
                    out_dir,
                    "kill_feed",
                    "kill_feed_area",
                    "score_change_window",
                    t,
                    float(event["hash_distance"]),
                    {
                        "score_event": event,
                        "actor_scope": actor_scope,
                        "kill_feed_actor": actor_info,
                        "local_player_name": args.local_player_name,
                        "local_team": args.local_team,
                        "actor_notes": actor_notes + list(actor_info.get("actor_notes", []) or []),
                    },
                )
            )
            if args.include_first_kill_medal:
                candidates.append(
                    save_candidate(
                        obs,
                        out_dir,
                        "first_kill_medal",
                        "kill_medal_area",
                        "score_change_window",
                        t,
                        float(event["hash_distance"]),
                        {
                            "score_event": event,
                            "actor_scope": "own_kill_possible",
                            "local_player_name": args.local_player_name,
                            "local_team": args.local_team,
                            "actor_notes": [
                                "first-kill medal is a player-local reward cue, but should still be cross-checked with kill feed/count timing"
                            ],
                        },
                    )
                )

    for event in death_events:
        t = float(event["time_sec"])
        in_window = [
            obs for obs in observations
            if obs.video_id == event["video_id"]
            and t - args.death_pre_sec <= obs.timestamp_sec <= t + args.death_post_sec
            and is_death_observation(obs, args)
        ]
        for obs in in_window:
            candidates.append(
                save_candidate(
                    obs,
                    out_dir,
                    "death_killer_panel",
                    "death_killer_panel",
                    "death_panel_peak_window",
                    t,
                    obs.death_panel_score,
                    {
                        "death_event": event,
                        "features": obs.death_panel_features,
                        "killer_cue_score": obs.killer_cue_score,
                        "killer_cue_features": obs.killer_cue_features,
                        "alive_hud_score": obs.alive_hud_score,
                        "alive_hud_features": obs.alive_hud_features,
                    },
                )
            )
    return candidates


def dedupe_candidates(candidates: list[TemporalCandidate], threshold: int) -> tuple[list[TemporalCandidate], dict[str, str], dict[str, str]]:
    representatives: list[TemporalCandidate] = []
    duplicate_to_rep: dict[str, str] = {}
    hashes: dict[str, str] = {}
    rep_hashes_by_class: dict[str, list[tuple[str, str]]] = {}

    for candidate in candidates:
        h = image_dhash(cv2.imread(candidate.image_path))
        hashes[candidate.candidate_id] = h
        duplicate_of = None
        for rep_id, rep_hash in rep_hashes_by_class.get(candidate.class_name, []):
            if hamming_hex(h, rep_hash) <= threshold:
                duplicate_of = rep_id
                break
        if duplicate_of:
            duplicate_to_rep[candidate.candidate_id] = duplicate_of
            continue
        representatives.append(candidate)
        rep_hashes_by_class.setdefault(candidate.class_name, []).append((candidate.candidate_id, h))
    return representatives, duplicate_to_rep, hashes


def make_contact_sheet(candidates: list[TemporalCandidate], out_path: Path, thumb_size: tuple[int, int] = (220, 110), cols: int = 4) -> None:
    if not candidates:
        return
    thumbs = []
    for candidate in candidates:
        img = cv2.imread(candidate.image_path)
        if img is None:
            continue
        thumb = cv2.resize(img, thumb_size, interpolation=cv2.INTER_AREA)
        label = f"{candidate.frame_index} {candidate.timestamp_sec:.1f}s"
        cv2.putText(thumb, label, (5, thumb_size[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
        thumbs.append(thumb)
    if not thumbs:
        return
    rows = math.ceil(len(thumbs) / cols)
    canvas = np.zeros((rows * thumb_size[1], cols * thumb_size[0], 3), dtype=np.uint8)
    for idx, thumb in enumerate(thumbs):
        y = (idx // cols) * thumb_size[1]
        x = (idx % cols) * thumb_size[0]
        canvas[y:y + thumb_size[1], x:x + thumb_size[0]] = thumb
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), canvas)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine notification candidates from temporal event windows, without VLM or automatic promotion."
    )
    parser.add_argument("--dataset", required=True, help="Dataset directory or a single video file.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--resize", default="1920x1080")
    parser.add_argument("--base-resolution", default="1920x1080")
    parser.add_argument("--roi-config", default=_default_config("roi_config.example.json"))
    parser.add_argument("--score-config", default=_default_config("score_reader_config.example.json"))
    parser.add_argument("--ui-templates", default=None)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--include-non-gameplay", action="store_true")
    parser.add_argument("--include-first-kill-medal", action="store_true")
    parser.add_argument(
        "--local-player-name",
        default=None,
        help="Optional player name used later to distinguish own_kill from teammate/enemy kill after OCR/name extraction.",
    )
    parser.add_argument(
        "--local-team",
        choices=["GR", "BL"],
        default=None,
        help="Optional local team. Score changes are team-level evidence, not proof of an own kill.",
    )
    parser.add_argument(
        "--use-easyocr",
        action="store_true",
        help="Deprecated compatibility flag. EasyOCR actor parsing is enabled by default.",
    )
    parser.add_argument(
        "--disable-easyocr",
        action="store_true",
        help="Disable default EasyOCR actor parsing and keep kill-feed ownership unresolved.",
    )
    parser.add_argument(
        "--install-easyocr",
        action="store_true",
        help="Attempt pip install easyocr before OCR actor parsing. Installation is explicit, never automatic.",
    )
    parser.add_argument("--user-install", action="store_true", help="Use pip install --user with --install-easyocr.")
    parser.add_argument("--upgrade-install", action="store_true", help="Use pip install --upgrade with --install-easyocr.")
    parser.add_argument(
        "--require-easyocr",
        action="store_true",
        help="Fail instead of falling back to unresolved actor_scope when EasyOCR is unavailable.",
    )
    parser.add_argument(
        "--easyocr-model-dir",
        default="outputs/easyocr_models",
        help="Writable model/cache directory for EasyOCR actor parsing.",
    )
    parser.add_argument(
        "--ocr-min-confidence",
        type=float,
        default=0.35,
        help="Ignore OCR text below this confidence when resolving kill-feed actor ownership.",
    )
    parser.add_argument(
        "--disable-ocr-preprocess",
        action="store_true",
        help="Disable upscale/CLAHE/sharpening before EasyOCR.",
    )
    parser.add_argument(
        "--ocr-scale",
        type=int,
        default=3,
        help="Upscale factor for OCR preprocessing.",
    )
    parser.add_argument("--score-change-hash-threshold", type=int, default=8)
    parser.add_argument("--min-event-gap-sec", type=float, default=1.0)
    parser.add_argument("--kill-pre-sec", type=float, default=0.6)
    parser.add_argument("--kill-post-sec", type=float, default=1.6)
    parser.add_argument("--max-frames-per-score-event", type=int, default=16)
    parser.add_argument("--death-panel-min-score", type=float, default=0.55)
    parser.add_argument("--killer-cue-min-score", type=float, default=0.65)
    parser.add_argument(
        "--alive-hud-max-score",
        type=float,
        default=0.30,
        help="Reject death candidates when normal HP/weapon HUD is still strongly visible.",
    )
    parser.add_argument(
        "--killer-cue-box",
        default="750,375,330,135",
        help="Full-frame x,y,w,h area where the central KILLER label appears on 1920x1080 frames.",
    )
    parser.add_argument("--death-merge-gap-sec", type=float, default=1.0)
    parser.add_argument("--death-pre-sec", type=float, default=0.4)
    parser.add_argument("--death-post-sec", type=float, default=1.2)
    parser.add_argument("--dedupe-threshold", type=int, default=8)
    parser.add_argument("--max-frames", type=int, default=None)
    args = parser.parse_args()
    args.use_easyocr = bool(args.use_easyocr or not args.disable_easyocr)
    args.ocr_preprocess = not args.disable_ocr_preprocess

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = discover_videos(Path(args.dataset))
    install_status = None
    if args.install_easyocr:
        install_status = ensure_easyocr(user=args.user_install, upgrade=args.upgrade_install, dry_run=False)
        if not install_status.available_after:
            print(f"[WARN] easyocr installation failed: {install_status.error}", file=sys.stderr)

    if args.require_easyocr and not is_import_available("easyocr"):
        raise RuntimeError("EasyOCR is required but not installed. Re-run with --install-easyocr or install easyocr manually.")

    ocr = OptionalEasyOCR(enabled=args.use_easyocr, model_dir=args.easyocr_model_dir)
    if args.use_easyocr and not ocr.available:
        print(f"[WARN] EasyOCR requested but unavailable: {ocr.error}", file=sys.stderr)
        if args.require_easyocr:
            raise RuntimeError(f"EasyOCR is required but unavailable: {ocr.error}")

    all_observations: list[FrameObservation] = []
    by_video: dict[str, dict[str, Any]] = {}
    for video in videos:
        print(f"[collect] {video.name}", flush=True)
        observations = collect_observations(video, args)
        score_events = detect_score_change_events(observations, args)
        death_events = group_death_panel_peaks(observations, args)
        all_observations.extend(observations)
        by_video[_safe_name(video.stem)] = {
            "video": str(video),
            "num_observations": len(observations),
            "score_events": score_events,
            "death_events": death_events,
        }

    all_score_events = [e for video_info in by_video.values() for e in video_info["score_events"]]
    all_death_events = [e for video_info in by_video.values() for e in video_info["death_events"]]
    candidates = mine_candidates(all_observations, all_score_events, all_death_events, out_dir, args, ocr)
    representatives, duplicate_to_rep, hashes = dedupe_candidates(candidates, args.dedupe_threshold)

    for class_name in sorted({c.class_name for c in candidates}):
        reps = [c for c in representatives if c.class_name == class_name]
        make_contact_sheet(reps, out_dir / f"contact_sheet_{class_name}.jpg")

    report = {
        "method": "temporal_event_window_mining",
        "description": (
            "Candidates are mined near visual score changes for kill feed/medal, "
            "and near high-scoring death_killer_panel visual peaks for death panels. "
            "No VLM labels and no automatic template promotion are used."
        ),
        "source_videos": [str(v) for v in videos],
        "config": {
            "sample_fps": args.sample_fps,
            "score_config": args.score_config,
            "roi_config": args.roi_config,
            "include_first_kill_medal": args.include_first_kill_medal,
            "local_player_name": args.local_player_name,
            "local_team": args.local_team,
            "use_easyocr": args.use_easyocr,
            "install_easyocr": args.install_easyocr,
            "easyocr_install_status": asdict(install_status) if install_status else None,
            "require_easyocr": args.require_easyocr,
            "easyocr_available": ocr.available,
            "easyocr_error": ocr.error,
            "easyocr_model_dir": args.easyocr_model_dir,
            "ocr_min_confidence": args.ocr_min_confidence,
            "ocr_preprocess": args.ocr_preprocess,
            "ocr_scale": args.ocr_scale,
            "actor_policy": {
                "kill_feed_without_name_parse": "team_or_global_kill_unresolved",
                "own_kill_requires": [
                    "killer_name == local_player_name",
                    "or strong player-local reward cue such as first_kill_medal, cross-checked with kill feed/count timing",
                ],
                "own_death_requires": [
                    "death_killer_panel",
                    "central KILLER cue",
                    "weak alive HUD",
                ],
            },
            "score_change_hash_threshold": args.score_change_hash_threshold,
            "death_panel_min_score": args.death_panel_min_score,
            "killer_cue_min_score": args.killer_cue_min_score,
            "alive_hud_max_score": args.alive_hud_max_score,
            "killer_cue_box": args.killer_cue_box,
            "dedupe_threshold": args.dedupe_threshold,
        },
        "by_video": by_video,
        "num_candidates": len(candidates),
        "num_representatives": len(representatives),
        "num_duplicates": len(duplicate_to_rep),
        "representative_ids": [c.candidate_id for c in representatives],
        "duplicate_to_representative": duplicate_to_rep,
        "hashes": hashes,
        "candidates": [asdict(c) for c in candidates],
    }
    _json_dump(report, out_dir / "temporal_notification_candidates.json")
    _json_dump([asdict(c) for c in representatives], out_dir / "representatives.json")

    print(
        json.dumps(
            {
                "report": str(out_dir / "temporal_notification_candidates.json"),
                "representatives": str(out_dir / "representatives.json"),
                "num_videos": len(videos),
                "num_score_events": len(all_score_events),
                "num_death_events": len(all_death_events),
                "num_candidates": len(candidates),
                "num_representatives": len(representatives),
                "num_duplicates": len(duplicate_to_rep),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
