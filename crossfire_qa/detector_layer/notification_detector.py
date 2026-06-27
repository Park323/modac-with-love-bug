from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import cv2
import numpy as np


NotificationClass = Literal[
    "kill_feed",
    "first_kill_medal",
    "death_killer_panel",
    "respawn_related",
    "none",
    "unknown",
]


DEFAULT_CLASS_THRESHOLDS: dict[str, float] = {
    "kill_feed": 0.58,
    "first_kill_medal": 0.58,
    "death_killer_panel": 0.55,
    "respawn_related": 0.55,
    "none": 0.0,
    "unknown": 0.0,
}

DEFAULT_TEMPLATE_THRESHOLDS: dict[str, float] = {
    "kill_feed": 0.62,
    "first_kill_medal": 0.62,
    "death_killer_panel": 0.60,
    "respawn_related": 0.60,
}

DEFAULT_REGION_CLASS_MAP: dict[str, str] = {
    "kill_feed_area": "kill_feed",
}

# Region names used by the UI detector and consumed by this module.
REQUIRED_NOTIFICATION_ROIS = [
    "kill_feed_area",
    "hp_ac_area",
    "weapon_ammo_area",
    "crosshair",
]


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    def to_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


@dataclass
class TemplateMatch:
    template_name: str
    score: float
    bbox: list[int]
    status: str = "ok"


@dataclass
class ImageFeatures:
    bright_ratio: float
    dark_ratio: float
    edge_density: float
    saturation_ratio: float
    text_like_density: float
    contrast: float


@dataclass
class NotificationSignal:
    class_name: NotificationClass
    roi_name: str
    confidence: float
    method: str
    status: str
    features: Optional[ImageFeatures] = None
    template_match: Optional[TemplateMatch] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class FrameNotificationReading:
    frame_index: int
    timestamp_sec: float
    crop_paths: dict[str, str]
    signals: list[NotificationSignal]
    top_class: NotificationClass
    top_confidence: float
    status: str


@dataclass
class NotificationEvent:
    time: float
    event: str
    class_name: NotificationClass
    confidence: float
    start_time: float
    end_time: float
    duration_sec: float
    frame_count: int
    source: list[str]
    status: str
    matched_count_change: Optional[dict[str, Any]] = None
    related_signals: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class NotificationTemplateLibrary:
    """
    Loads optional notification templates.

    Supported directory layouts:
      notification_templates/kill_feed/*.png
      notification_templates/kill_feed_row/*.png
      notification_templates/first_kill_medal/*.png
      notification_templates/death_killer_panel/*.png
      notification_templates/respawn_related/*.png

    Synonyms are normalized to the detector output classes:
      kill_feed_area -> kill_feed
      kill_feed_row -> kill_feed
      kill_medal_area -> first_kill_medal
      first_kill -> first_kill_medal
      killer_panel -> death_killer_panel
    """

    SYNONYMS: dict[str, str] = {
        "kill_feed": "kill_feed",
        "kill_feed_row": "kill_feed",
        "kill_feed_area": "kill_feed",
        "first_kill": "first_kill_medal",
        "first_kill_medal": "first_kill_medal",
        "kill_medal": "first_kill_medal",
        "kill_medal_area": "first_kill_medal",
        "death_killer_panel": "death_killer_panel",
        "killer_panel": "death_killer_panel",
        "killer": "death_killer_panel",
        "respawn": "respawn_related",
        "respawn_related": "respawn_related",
    }

    def __init__(self, template_dir: Optional[str | Path] = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else None
        self.templates: dict[str, list[tuple[str, np.ndarray]]] = {}
        if self.template_dir and self.template_dir.exists():
            self._load()

    @classmethod
    def normalize_key(cls, key: str) -> str:
        key = key.strip().lower()
        return cls.SYNONYMS.get(key, key)

    def _register(self, key: str, path: Path) -> None:
        norm_key = self.normalize_key(key)
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            return
        img = cv2.equalizeHist(img)
        self.templates.setdefault(norm_key, []).append((path.stem, img))

    def _load(self) -> None:
        assert self.template_dir is not None
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

        for p in self.template_dir.iterdir():
            if p.is_file() and p.suffix.lower() in image_exts:
                self._register(p.stem, p)

        for d in self.template_dir.iterdir():
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() in image_exts:
                    self._register(d.name, p)

    def get(self, class_name: str) -> list[tuple[str, np.ndarray]]:
        return self.templates.get(self.normalize_key(class_name), [])

    def has_templates(self, class_name: Optional[str] = None) -> bool:
        if class_name is not None:
            return len(self.get(class_name)) > 0
        return any(self.templates.values())

    def available_keys(self) -> list[str]:
        return sorted(k for k, v in self.templates.items() if v)


class NotificationDetector:
    """
    Kill/death notification detector for CrossFire QA.

    Input:
      crops produced by CrossFireUIDetector, especially:
        kill_feed_area, kill_medal_area, death_killer_panel,
        hp_ac_area, weapon_ammo_area, crosshair

    Output classes:
      kill_feed, first_kill_medal, death_killer_panel, respawn_related,
      none, unknown

    Design:
      - Template matching is used when templates exist.
      - Heuristic fallback is used to avoid a dead pipeline before templates exist.
      - none/unknown are explicit outputs to prevent forced kill/death decisions.
    """

    def __init__(
        self,
        template_dir: Optional[str | Path] = None,
        class_thresholds: Optional[dict[str, float]] = None,
        template_thresholds: Optional[dict[str, float]] = None,
        use_heuristics: bool = True,
    ) -> None:
        self.template_lib = NotificationTemplateLibrary(template_dir)
        self.class_thresholds = class_thresholds or DEFAULT_CLASS_THRESHOLDS
        self.template_thresholds = template_thresholds or DEFAULT_TEMPLATE_THRESHOLDS
        self.use_heuristics = use_heuristics

    @classmethod
    def from_json(cls, config_path: str | Path, **kwargs: Any) -> "NotificationDetector":
        with Path(config_path).open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cls(
            class_thresholds=cfg.get("class_thresholds", DEFAULT_CLASS_THRESHOLDS),
            template_thresholds=cfg.get("template_thresholds", DEFAULT_TEMPLATE_THRESHOLDS),
            **kwargs,
        )

    @staticmethod
    def extract_features(crop_bgr: np.ndarray) -> ImageFeatures:
        if crop_bgr.size == 0:
            return ImageFeatures(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr.copy()
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV) if crop_bgr.ndim == 3 else None

        bright_ratio = float(np.mean(gray > 175))
        dark_ratio = float(np.mean(gray < 55))
        contrast = float(np.std(gray) / 128.0)

        edges = cv2.Canny(gray, 80, 180)
        edge_density = float(np.mean(edges > 0))

        if hsv is not None:
            saturation_ratio = float(np.mean(hsv[:, :, 1] > 70))
        else:
            saturation_ratio = 0.0

        # Text-like density: small bright components, useful for kill feed rows and HUD text.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        text_like_area = 0
        total_area = max(1, gray.shape[0] * gray.shape[1])
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            area = w * h
            if 4 <= w <= 120 and 4 <= h <= 40 and 10 <= area <= 1800:
                aspect = w / max(1, h)
                if 0.15 <= aspect <= 8.0:
                    text_like_area += area
        text_like_density = float(text_like_area / total_area)

        return ImageFeatures(
            bright_ratio=max(0.0, min(1.0, bright_ratio)),
            dark_ratio=max(0.0, min(1.0, dark_ratio)),
            edge_density=max(0.0, min(1.0, edge_density)),
            saturation_ratio=max(0.0, min(1.0, saturation_ratio)),
            text_like_density=max(0.0, min(1.0, text_like_density)),
            contrast=max(0.0, min(2.0, contrast)),
        )

    @staticmethod
    def _infer_kill_feed_team_relation(crop_bgr: np.ndarray) -> dict[str, Any]:
        """Infer killer/victim teams from the kill feed text colors.

        CrossFire kill feed convention in this project:
          - warm/brown/orange text -> GR
          - blue/cyan text -> BL
        The left side of a feed row is treated as killer, the right side as victim.
        """
        if crop_bgr is None or crop_bgr.size == 0:
            return {"status": "missing_crop", "confidence": 0.0}
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        if h <= 0 or w <= 0:
            return {"status": "empty_crop", "confidence": 0.0}

        blue = (
            (hsv[:, :, 0] >= 85)
            & (hsv[:, :, 0] <= 125)
            & (hsv[:, :, 1] >= 45)
            & (hsv[:, :, 2] >= 75)
        )
        warm = (
            (hsv[:, :, 0] >= 5)
            & (hsv[:, :, 0] <= 35)
            & (hsv[:, :, 1] >= 35)
            & (hsv[:, :, 2] >= 70)
        )

        left = slice(None), slice(0, max(1, int(w * 0.46)))
        right = slice(None), slice(min(w - 1, int(w * 0.54)), None)
        left_blue = float(np.mean(blue[left]))
        left_warm = float(np.mean(warm[left]))
        right_blue = float(np.mean(blue[right]))
        right_warm = float(np.mean(warm[right]))

        def side_team(blue_ratio: float, warm_ratio: float) -> tuple[Optional[str], float]:
            total = blue_ratio + warm_ratio
            if total < 0.003:
                return None, 0.0
            margin = abs(blue_ratio - warm_ratio)
            if blue_ratio > warm_ratio:
                return "BL", min(1.0, margin / max(total, 1e-6) * min(1.0, total * 80.0))
            return "GR", min(1.0, margin / max(total, 1e-6) * min(1.0, total * 80.0))

        def opposite(team: Optional[str]) -> Optional[str]:
            if team == "BL":
                return "GR"
            if team == "GR":
                return "BL"
            return None

        killer_team, killer_conf = side_team(left_blue, left_warm)
        victim_team, victim_conf = side_team(right_blue, right_warm)
        inference_notes: list[str] = []
        high_conf = 0.55

        if killer_team and victim_team and killer_team == victim_team and killer_conf >= high_conf and victim_conf >= high_conf:
            inference_notes.append("same_team_color_conflict_rejected_by_no_teamkill_rule")
            killer_team = None
            victim_team = None
        elif killer_team and (not victim_team or victim_team == killer_team) and killer_conf >= high_conf:
            victim_team = opposite(killer_team)
            victim_conf = max(victim_conf, killer_conf * 0.80)
            inference_notes.append("victim_team_inferred_from_no_teamkill_rule")
        elif victim_team and (not killer_team or killer_team == victim_team) and victim_conf >= high_conf:
            killer_team = opposite(victim_team)
            killer_conf = max(killer_conf, victim_conf * 0.80)
            inference_notes.append("killer_team_inferred_from_no_teamkill_rule")

        expected_score_side = killer_team if killer_team and victim_team and killer_team != victim_team else None
        confidence = float(np.mean([c for c in [killer_conf, victim_conf] if c > 0.0])) if (killer_conf or victim_conf) else 0.0
        status = "ok" if expected_score_side and confidence >= 0.25 else "low_confidence"
        return {
            "status": status,
            "killer_team": killer_team,
            "victim_team": victim_team,
            "expected_score_side": expected_score_side,
            "confidence": max(0.0, min(1.0, confidence)),
            "inference_notes": inference_notes,
            "color_ratios": {
                "left_blue": left_blue,
                "left_warm": left_warm,
                "right_blue": right_blue,
                "right_warm": right_warm,
            },
            "assumption": "left colored text is killer; warm=GR, blue=BL",
        }

    @staticmethod
    def _match_template(crop_bgr: np.ndarray, template_gray: np.ndarray, template_name: str) -> Optional[TemplateMatch]:
        if crop_bgr.size == 0:
            return None
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr.copy()
        gray = cv2.equalizeHist(gray)
        th, tw = template_gray.shape[:2]
        h, w = gray.shape[:2]
        if th > h or tw > w:
            return None
        result = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        x, y = max_loc
        return TemplateMatch(template_name=template_name, score=float(max_val), bbox=[int(x), int(y), int(tw), int(th)])

    def _template_signal(self, crop_bgr: np.ndarray, roi_name: str, class_name: NotificationClass) -> Optional[NotificationSignal]:
        templates = self.template_lib.get(class_name)
        if not templates:
            return None

        best: Optional[TemplateMatch] = None
        for tpl_name, tpl_gray in templates:
            match = self._match_template(crop_bgr, tpl_gray, tpl_name)
            if match is not None and (best is None or match.score > best.score):
                best = match

        if best is None:
            return NotificationSignal(
                class_name="unknown",
                roi_name=roi_name,
                confidence=0.0,
                method="template",
                status="template_too_large_or_match_failed",
            )

        threshold = float(self.template_thresholds.get(class_name, 0.60))
        status = "ok" if best.score >= threshold else "low_confidence"
        return NotificationSignal(
            class_name=class_name,
            roi_name=roi_name,
            confidence=max(0.0, min(1.0, best.score)),
            method="template",
            status=status,
            template_match=best,
        )

    def _heuristic_signal(self, crop_bgr: np.ndarray, roi_name: str, class_name: NotificationClass) -> NotificationSignal:
        f = self.extract_features(crop_bgr)

        if class_name == "kill_feed":
            # Kill feed is small text/icons on right-top. Text density and edges are the strongest cheap clues.
            score = (
                1.8 * f.text_like_density
                + 1.1 * f.edge_density
                + 0.7 * f.bright_ratio
                + 0.4 * f.saturation_ratio
                + 0.3 * min(1.0, f.contrast)
            )
            score = min(0.78, score * 1.7)
        elif class_name == "first_kill_medal":
            # Kill medal is a bright/saturated central overlay. This is intentionally conservative.
            score = (
                0.9 * f.bright_ratio
                + 0.9 * f.saturation_ratio
                + 0.7 * f.edge_density
                + 0.5 * min(1.0, f.contrast)
            )
            score = min(0.76, score * 1.35)
        elif class_name == "death_killer_panel":
            # Killer panel combines a dark panel *and* bright text/lines.
            # Darkness alone is not enough because the fixed ROI can include plain dark game background.
            has_panel_detail = f.text_like_density > 0.010 or f.edge_density > 0.015 or f.bright_ratio > 0.010
            panel_dark_cue = f.dark_ratio if has_panel_detail else 0.0
            score = (
                0.35 * panel_dark_cue
                + 2.8 * f.text_like_density
                + 1.8 * f.edge_density
                + 0.8 * f.bright_ratio
                + 0.35 * min(1.0, f.contrast)
            )
            score = min(0.80, score)
        elif class_name == "respawn_related":
            # Respawn/normal HUD return: bright HUD text or crosshair reappearing.
            score = (
                1.0 * f.text_like_density
                + 0.9 * f.edge_density
                + 0.6 * f.bright_ratio
                + 0.2 * min(1.0, f.contrast)
            )
            score = min(0.72, score * 1.4)
        else:
            score = 0.0

        threshold = float(self.class_thresholds.get(class_name, 0.5))
        status = "ok" if score >= threshold else "low_confidence"
        return NotificationSignal(
            class_name=class_name,
            roi_name=roi_name,
            confidence=max(0.0, min(1.0, float(score))),
            method="heuristic",
            status=status,
            features=f,
            notes=["heuristic_fallback; add templates for stronger confidence"],
        )

    def _death_panel_structure_signal(self, crop_bgr: np.ndarray, roi_name: str) -> Optional[NotificationSignal]:
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr.copy()
        h, _ = gray.shape[:2]
        split = max(1, int(h * 0.45))
        upper = gray[:split, :]
        lower = gray[split:, :]
        upper_edges = cv2.Canny(upper, 80, 180)

        upper_bright = float(np.mean(upper > 150))
        upper_edge_density = float(np.mean(upper_edges > 0))
        lower_dark = float(np.mean(lower < 55))
        lower_mean = float(np.mean(lower))

        if upper_bright < 0.020 or upper_edge_density < 0.030 or lower_dark < 0.850 or lower_mean > 65.0:
            return None

        confidence = min(0.82, 0.45 + 5.0 * upper_bright + 2.5 * upper_edge_density + 0.15 * lower_dark)
        threshold = float(self.class_thresholds.get("death_killer_panel", 0.55))
        status = "ok" if confidence >= threshold else "low_confidence"
        return NotificationSignal(
            class_name="death_killer_panel",
            roi_name=roi_name,
            confidence=max(0.0, min(1.0, confidence)),
            method="death_panel_structure",
            status=status,
            features=self.extract_features(crop_bgr),
            notes=[
                f"upper_bright={upper_bright:.3f}",
                f"upper_edge_density={upper_edge_density:.3f}",
                f"lower_dark={lower_dark:.3f}",
            ],
        )

    def detect_frame(
        self,
        crops: dict[str, np.ndarray],
        frame_index: int,
        timestamp_sec: float,
        crop_paths: Optional[dict[str, str]] = None,
    ) -> FrameNotificationReading:
        crop_paths = crop_paths or {}
        signals: list[NotificationSignal] = []

        for roi_name, class_name in DEFAULT_REGION_CLASS_MAP.items():
            crop = crops.get(roi_name)
            if crop is None:
                signals.append(
                    NotificationSignal(
                        class_name="unknown",
                        roi_name=roi_name,
                        confidence=0.0,
                        method="missing_crop",
                        status="missing_crop",
                    )
                )
                continue

            signal = self._template_signal(crop, roi_name, class_name)  # type: ignore[arg-type]
            if class_name == "death_killer_panel":
                structure_signal = self._death_panel_structure_signal(crop, roi_name)
                if structure_signal is not None and (signal is None or structure_signal.confidence > signal.confidence):
                    signal = structure_signal
            if signal is None and self.use_heuristics:
                signal = self._heuristic_signal(crop, roi_name, class_name)  # type: ignore[arg-type]
            elif signal is None:
                signal = NotificationSignal(
                    class_name=class_name,  # type: ignore[arg-type]
                    roi_name=roi_name,
                    confidence=0.0,
                    method="none",
                    status="no_template",
                )
            if class_name == "kill_feed":
                team_relation = self._infer_kill_feed_team_relation(crop)
                signal.metadata["team_relation"] = team_relation
                if team_relation.get("status") != "ok":
                    signal.confidence = min(signal.confidence, 0.20)
                    signal.status = "low_confidence"
                    signal.notes.append("kill_feed_team_color_relation_not_confirmed")
            signals.append(signal)

        # Respawn-related signal uses normal HUD regions instead of a single notification ROI.
        respawn_signals: list[NotificationSignal] = []
        for roi_name in ["hp_ac_area", "weapon_ammo_area", "crosshair"]:
            crop = crops.get(roi_name)
            if crop is None:
                continue
            respawn_signals.append(self._heuristic_signal(crop, roi_name, "respawn_related"))
        if respawn_signals:
            avg_conf = float(np.mean([s.confidence for s in respawn_signals]))
            status = "ok" if avg_conf >= self.class_thresholds.get("respawn_related", 0.55) else "low_confidence"
            signals.append(
                NotificationSignal(
                    class_name="respawn_related",
                    roi_name="hp_ac_area+weapon_ammo_area+crosshair",
                    confidence=avg_conf,
                    method="hud_return_heuristic",
                    status=status,
                    notes=["normal HUD/crosshair return cue; useful after death_killer_panel"],
                )
            )

        positive_signals = [s for s in signals if s.class_name not in {"none", "unknown"}]
        if not positive_signals:
            top_class: NotificationClass = "unknown"
            top_confidence = 0.0
            status = "no_signal"
        else:
            best = max(positive_signals, key=lambda s: s.confidence)
            threshold = self.class_thresholds.get(best.class_name, 0.5)
            if best.confidence >= threshold:
                top_class = best.class_name
                top_confidence = best.confidence
                status = "ok"
            elif best.confidence > 0.15:
                top_class = "unknown"
                top_confidence = best.confidence
                status = "low_confidence"
            else:
                top_class = "none"
                top_confidence = best.confidence
                status = "none"

        return FrameNotificationReading(
            frame_index=frame_index,
            timestamp_sec=timestamp_sec,
            crop_paths=crop_paths,
            signals=signals,
            top_class=top_class,
            top_confidence=float(top_confidence),
            status=status,
        )


class NotificationTemporalAggregator:
    """Converts frame-level notification signals into event-level kill/death timeline."""

    def __init__(
        self,
        class_thresholds: Optional[dict[str, float]] = None,
        merge_gap_sec: float = 1.0,
        min_votes: int = 2,
        kill_signal_window_sec: float = 2.0,
        death_respawn_window_sec: float = 5.0,
        count_match_window_sec: float = 2.0,
        allow_medal_only_kill: bool = False,
    ) -> None:
        self.class_thresholds = class_thresholds or DEFAULT_CLASS_THRESHOLDS
        self.merge_gap_sec = merge_gap_sec
        self.min_votes = min_votes
        self.kill_signal_window_sec = kill_signal_window_sec
        self.death_respawn_window_sec = death_respawn_window_sec
        self.count_match_window_sec = count_match_window_sec
        self.allow_medal_only_kill = allow_medal_only_kill

    def _signal_threshold(self, class_name: str) -> float:
        return float(self.class_thresholds.get(class_name, 0.5))

    def _collect_class_hits(self, frame_readings: list[FrameNotificationReading], class_name: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        threshold = self._signal_threshold(class_name)
        for fr in frame_readings:
            for sig in fr.signals:
                if sig.class_name != class_name:
                    continue
                if sig.confidence < threshold:
                    continue
                hits.append(
                    {
                        "time": fr.timestamp_sec,
                        "frame_index": fr.frame_index,
                        "confidence": sig.confidence,
                        "roi_name": sig.roi_name,
                        "method": sig.method,
                        "status": sig.status,
                        "metadata": sig.metadata,
                    }
                )
        return sorted(hits, key=lambda x: x["time"])

    @staticmethod
    def _merge_team_relation(group: dict[str, Any]) -> Optional[dict[str, Any]]:
        relations: list[dict[str, Any]] = []
        for hit in group.get("hits", []):
            metadata = hit.get("metadata", {}) if isinstance(hit.get("metadata"), dict) else {}
            relation = metadata.get("team_relation")
            if isinstance(relation, dict) and relation.get("expected_score_side"):
                relations.append(relation)
        if not relations:
            return None
        scores: dict[str, float] = {}
        for rel in relations:
            side = str(rel.get("expected_score_side"))
            scores[side] = scores.get(side, 0.0) + float(rel.get("confidence", 0.0) or 0.0)
        expected = max(scores, key=scores.get)
        selected = [r for r in relations if r.get("expected_score_side") == expected]
        best = max(selected, key=lambda r: float(r.get("confidence", 0.0) or 0.0))
        merged = dict(best)
        merged["confidence"] = float(np.mean([float(r.get("confidence", 0.0) or 0.0) for r in selected]))
        merged["votes"] = len(selected)
        return merged

    def _group_hits(self, hits: list[dict[str, Any]], class_name: str) -> list[dict[str, Any]]:
        if not hits:
            return []
        groups: list[list[dict[str, Any]]] = [[hits[0]]]
        for hit in hits[1:]:
            if hit["time"] - groups[-1][-1]["time"] <= self.merge_gap_sec:
                groups[-1].append(hit)
            else:
                groups.append([hit])

        out: list[dict[str, Any]] = []
        for g in groups:
            confs = [float(h["confidence"]) for h in g]
            start = float(g[0]["time"])
            end = float(g[-1]["time"])
            frame_count = len({int(h["frame_index"]) for h in g})
            status = "CONFIRMED" if frame_count >= self.min_votes else "INFERRED"
            if frame_count < self.min_votes and max(confs) < 0.75:
                status = "UNCERTAIN"
            out.append(
                {
                    "class_name": class_name,
                    "start_time": start,
                    "end_time": end,
                    "time": float(np.average([h["time"] for h in g], weights=confs)),
                    "duration_sec": max(0.0, end - start),
                    "frame_count": frame_count,
                    "confidence": float(np.mean(confs)),
                    "max_confidence": float(max(confs)),
                    "hits": g,
                    "status": status,
                }
            )
        return out

    @staticmethod
    def _groups_overlap_or_near(a: dict[str, Any], b: dict[str, Any], window_sec: float) -> bool:
        return not (a["end_time"] + window_sec < b["start_time"] or b["end_time"] + window_sec < a["start_time"])

    def _find_nearest_count_change(self, time_sec: float, count_events: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        best: Optional[dict[str, Any]] = None
        best_dt = math.inf
        for ev in count_events:
            if ev.get("event") != "count_change":
                continue
            t = float(ev.get("time", 0.0))
            dt = abs(t - time_sec)
            if dt <= self.count_match_window_sec and dt < best_dt:
                best = ev
                best_dt = dt
        return best

    def aggregate(
        self,
        frame_readings: list[FrameNotificationReading],
        count_change_events: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        count_change_events = count_change_events or []

        grouped: dict[str, list[dict[str, Any]]] = {}
        for class_name in ["kill_feed", "respawn_related"]:
            grouped[class_name] = self._group_hits(self._collect_class_hits(frame_readings, class_name), class_name)

        events: list[NotificationEvent] = []

        # Kill notification: only kill feed is used. Medal/death-panel signals are ignored.
        for feed in grouped["kill_feed"]:
            related = [feed]
            count_match = self._find_nearest_count_change(feed["time"], count_change_events)
            source = ["kill_feed"]
            if count_match is not None:
                source.append("count_change_crosscheck")

            conf_values = [g["confidence"] for g in related]
            if count_match is not None:
                conf_values.append(float(count_match.get("confidence", 0.7)))
            confidence = float(np.mean(conf_values))
            status = "CONFIRMED" if len(source) >= 2 or feed["status"] == "CONFIRMED" else feed["status"]
            if confidence < 0.58:
                status = "UNCERTAIN"
            team_relation = self._merge_team_relation(feed)

            event_dict = asdict(
                NotificationEvent(
                    time=float(feed["time"]),
                    event="kill_notification",
                    class_name="kill_feed",
                    confidence=confidence,
                    start_time=float(min(g["start_time"] for g in related)),
                    end_time=float(max(g["end_time"] for g in related)),
                    duration_sec=float(max(g["end_time"] for g in related) - min(g["start_time"] for g in related)),
                    frame_count=int(sum(g["frame_count"] for g in related)),
                    source=source,
                    status=status,
                    matched_count_change=count_match,
                    related_signals=related,
                    notes=["kill_feed_only"],
                )
            )
            if team_relation:
                event_dict["team_relation"] = team_relation
                event_dict["expected_score_side"] = team_relation.get("expected_score_side")
            events.append(event_dict)

        # Respawn-related standalone groups are retained as lower-level evidence, not final pass/fail.
        for respawn in grouped["respawn_related"]:
            events.append(
                asdict(NotificationEvent(
                    time=float(respawn["time"]),
                    event="respawn_related",
                    class_name="respawn_related",
                    confidence=float(respawn["confidence"]),
                    start_time=float(respawn["start_time"]),
                    end_time=float(respawn["end_time"]),
                    duration_sec=float(respawn["duration_sec"]),
                    frame_count=int(respawn["frame_count"]),
                    source=["hud_return_heuristic"],
                    status=respawn["status"],
                    related_signals=[respawn],
                    notes=["supporting evidence; final respawn validation belongs to respawn segment/spawn recognizer"],
                ))
            )

        events = sorted(events, key=lambda e: (e["time"], e["event"]))
        return {
            "grouped_signals": grouped,
            "events": events,
        }


def frame_readings_to_dict(frame_readings: Iterable[FrameNotificationReading]) -> list[dict[str, Any]]:
    return [asdict(fr) for fr in frame_readings]


def load_notification_config(config_path: Optional[str | Path]) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    if not config_path:
        return DEFAULT_CLASS_THRESHOLDS, DEFAULT_TEMPLATE_THRESHOLDS, {}
    with Path(config_path).open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    return (
        cfg.get("class_thresholds", DEFAULT_CLASS_THRESHOLDS),
        cfg.get("template_thresholds", DEFAULT_TEMPLATE_THRESHOLDS),
        cfg.get("temporal", {}),
    )


def load_count_change_events(kill_count_report_path: Optional[str | Path]) -> list[dict[str, Any]]:
    if not kill_count_report_path:
        return []
    with Path(kill_count_report_path).open("r", encoding="utf-8") as f:
        report = json.load(f)
    return report.get("temporal_aggregation", {}).get("events", [])
