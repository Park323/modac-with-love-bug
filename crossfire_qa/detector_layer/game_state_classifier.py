"""
hp_ac_area + weapon_ammo_area + crosshair
→ alive_playing 판단

death_killer_panel
→ killer_panel / dead_or_killcam 판단

kill_medal_area + notification_report
→ kill_confirmed_overlay 판단

dead_or_killcam → alive_playing 전이
→ respawn_candidate 판단


출력
frame_readings
  프레임별 raw state 판정

temporal_aggregation.stable_series
  smoothing된 state timeline

temporal_aggregation.segments
  alive / dead / killer_panel 등의 연속 구간

temporal_aggregation.events
  death_state_entered
  kill_overlay_entered
  respawn_candidate
  state_change
"""


from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import cv2
import numpy as np


GameStateClass = Literal[
    "alive_playing",
    "engaging_enemy",
    "kill_confirmed_overlay",
    "dead_or_killcam",
    "killer_panel",
    "respawned_playing",
    "menu_or_loading",
    "unknown",
]


STATE_ROIS = [
    "top_score_bar",
    "kill_feed_area",
    "kill_medal_area",
    "death_killer_panel",
    "minimap",
    "location_text",
    "hp_ac_area",
    "weapon_ammo_area",
    "crosshair",
]


DEFAULT_STATE_THRESHOLDS: dict[str, float] = {
    "alive_playing": 0.50,
    "engaging_enemy": 0.70,
    "kill_confirmed_overlay": 0.55,
    "dead_or_killcam": 0.55,
    "killer_panel": 0.55,
    "respawned_playing": 0.50,
    "menu_or_loading": 0.60,
    "unknown": 0.0,
}


DEFAULT_STATE_CONFIG: dict[str, Any] = {
    "state_thresholds": DEFAULT_STATE_THRESHOLDS,
    "temporal": {
        "smoothing_window_sec": 0.8,
        "merge_gap_sec": 0.8,
        "min_segment_duration_sec": 0.2,
        "respawn_after_death_window_sec": 8.0,
        "stable_alive_after_respawn_sec": 0.5,
    },
    "feature_thresholds": {
        "hud_presence_min": 0.18,
        "crosshair_presence_min": 0.10,
        "low_hud_presence_max": 0.12,
        "template_min_score": 0.60,
    },
}


@dataclass
class ImageFeatures:
    bright_ratio: float
    dark_ratio: float
    edge_density: float
    saturation_ratio: float
    text_like_density: float
    contrast: float
    mean_luma: float
    non_black_ratio: float


@dataclass
class TemplateMatch:
    template_name: str
    score: float
    bbox: list[int]
    status: str = "ok"


@dataclass
class StateSignal:
    name: str
    confidence: float
    status: str
    method: str
    roi_name: Optional[str] = None
    features: Optional[ImageFeatures] = None
    template_match: Optional[TemplateMatch] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class FrameStateReading:
    frame_index: int
    timestamp_sec: float
    crop_paths: dict[str, str]
    state: GameStateClass
    confidence: float
    status: str
    scores: dict[str, float]
    signals: list[StateSignal]
    notification_hint: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class StateSegment:
    state: GameStateClass
    start_time: float
    end_time: float
    duration_sec: float
    frame_count: int
    confidence: float
    start_frame_index: int
    end_frame_index: int
    status: str
    notes: list[str] = field(default_factory=list)


@dataclass
class StateTransitionEvent:
    time: float
    event: str
    from_state: GameStateClass
    to_state: GameStateClass
    confidence: float
    status: str
    source: list[str]
    notes: list[str] = field(default_factory=list)


class StateTemplateLibrary:
    """
    Optional state template loader.

    Supported layouts:
      state_templates/killer_panel/*.png
      state_templates/dead_or_killcam/*.png
      state_templates/kill_confirmed_overlay/*.png
      state_templates/alive_playing/*.png
      state_templates/menu_or_loading/*.png

    Templates are optional. If none exist, the classifier falls back to conservative
    HUD-presence heuristics and notification-report hints.
    """

    SYNONYMS: dict[str, str] = {
        "alive": "alive_playing",
        "alive_playing": "alive_playing",
        "playing": "alive_playing",
        "engaging_enemy": "engaging_enemy",
        "kill_overlay": "kill_confirmed_overlay",
        "kill_confirmed": "kill_confirmed_overlay",
        "kill_confirmed_overlay": "kill_confirmed_overlay",
        "first_kill_medal": "kill_confirmed_overlay",
        "dead": "dead_or_killcam",
        "death": "dead_or_killcam",
        "dead_or_killcam": "dead_or_killcam",
        "killcam": "dead_or_killcam",
        "killer": "killer_panel",
        "killer_panel": "killer_panel",
        "death_killer_panel": "killer_panel",
        "respawn": "respawned_playing",
        "respawned": "respawned_playing",
        "respawned_playing": "respawned_playing",
        "menu": "menu_or_loading",
        "loading": "menu_or_loading",
        "menu_or_loading": "menu_or_loading",
    }

    def __init__(self, template_dir: Optional[str | Path] = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else None
        self.templates: dict[str, list[tuple[str, np.ndarray]]] = {}
        if self.template_dir and self.template_dir.exists():
            self._load()

    @classmethod
    def normalize_key(cls, key: str) -> str:
        return cls.SYNONYMS.get(key.strip().lower(), key.strip().lower())

    def _register(self, key: str, path: Path) -> None:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            return
        img = cv2.equalizeHist(img)
        self.templates.setdefault(self.normalize_key(key), []).append((path.stem, img))

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

    def get(self, state: str) -> list[tuple[str, np.ndarray]]:
        return self.templates.get(self.normalize_key(state), [])

    def available_keys(self) -> list[str]:
        return sorted(k for k, v in self.templates.items() if v)


class GameStateClassifier:
    """
    CrossFire frame-level game state classifier.

    Input:
      ROI crops from CrossFireUIDetector and optional notification hints.

    Output states:
      alive_playing, engaging_enemy, kill_confirmed_overlay, dead_or_killcam,
      killer_panel, respawned_playing, menu_or_loading, unknown

    MVP strategy:
      - Use notification report hints when available.
      - Use optional templates for high-confidence states.
      - Use conservative HUD-presence heuristics as a fallback.
      - Never force a class when evidence is weak; return unknown instead.
    """

    def __init__(
        self,
        template_dir: Optional[str | Path] = None,
        state_thresholds: Optional[dict[str, float]] = None,
        feature_thresholds: Optional[dict[str, float]] = None,
        use_heuristics: bool = True,
    ) -> None:
        self.template_lib = StateTemplateLibrary(template_dir)
        self.state_thresholds = state_thresholds or DEFAULT_STATE_THRESHOLDS.copy()
        self.feature_thresholds = DEFAULT_STATE_CONFIG["feature_thresholds"].copy()
        if feature_thresholds:
            self.feature_thresholds.update(feature_thresholds)
        self.use_heuristics = use_heuristics

    @staticmethod
    def extract_features(crop_bgr: np.ndarray) -> ImageFeatures:
        if crop_bgr is None or crop_bgr.size == 0:
            return ImageFeatures(0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
        edges = cv2.Canny(gray, 60, 140)

        bright_ratio = float(np.mean(gray > 185))
        dark_ratio = float(np.mean(gray < 35))
        edge_density = float(np.mean(edges > 0))
        saturation_ratio = float(np.mean(hsv[:, :, 1] > 70))
        contrast = float(np.std(gray) / 128.0)
        mean_luma = float(np.mean(gray) / 255.0)
        non_black_ratio = float(np.mean(gray > 20))

        # Small UI text usually creates thin high-contrast edges but not too much area.
        text_like_density = float(min(1.0, edge_density * 3.0 + bright_ratio * 0.35 + contrast * 0.20))

        return ImageFeatures(
            bright_ratio=bright_ratio,
            dark_ratio=dark_ratio,
            edge_density=edge_density,
            saturation_ratio=saturation_ratio,
            text_like_density=text_like_density,
            contrast=contrast,
            mean_luma=mean_luma,
            non_black_ratio=non_black_ratio,
        )

    @staticmethod
    def _preprocess_gray(img_bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        return cv2.equalizeHist(gray)

    @staticmethod
    def _match_template(search_bgr: np.ndarray, template_gray: np.ndarray, template_name: str) -> Optional[TemplateMatch]:
        if search_bgr is None or search_bgr.size == 0:
            return None
        search_gray = GameStateClassifier._preprocess_gray(search_bgr)
        th, tw = template_gray.shape[:2]
        sh, sw = search_gray.shape[:2]
        if th > sh or tw > sw:
            return None
        result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        x, y = max_loc
        return TemplateMatch(template_name=template_name, score=float(max_val), bbox=[int(x), int(y), int(tw), int(th)])

    def _best_template_score(self, crops: dict[str, np.ndarray], state: str) -> tuple[float, Optional[TemplateMatch], Optional[str]]:
        templates = self.template_lib.get(state)
        if not templates:
            return 0.0, None, None

        # State templates are usually tied to these ROIs. Trying all crops keeps the bootstrap flexible.
        best_score = 0.0
        best_match: Optional[TemplateMatch] = None
        best_roi: Optional[str] = None
        for roi_name, crop in crops.items():
            for tpl_name, tpl_gray in templates:
                match = self._match_template(crop, tpl_gray, f"{state}:{tpl_name}")
                if match is None:
                    continue
                if match.score > best_score:
                    best_score = match.score
                    best_match = match
                    best_roi = roi_name
        return best_score, best_match, best_roi

    @staticmethod
    def _clamp01(value: float) -> float:
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return float(max(0.0, min(1.0, value)))

    def _hud_presence_score(self, features: ImageFeatures) -> float:
        score = (
            0.35 * min(1.0, features.edge_density / 0.12)
            + 0.25 * min(1.0, features.contrast / 0.45)
            + 0.20 * min(1.0, features.bright_ratio / 0.12)
            + 0.20 * min(1.0, features.saturation_ratio / 0.25)
        )
        return self._clamp01(score)

    def _crosshair_presence_score(self, features: ImageFeatures) -> float:
        # Crosshair crop is small. A visible crosshair often creates a few sharp bright edges.
        score = (
            0.45 * min(1.0, features.edge_density / 0.08)
            + 0.35 * min(1.0, features.bright_ratio / 0.08)
            + 0.20 * min(1.0, features.contrast / 0.35)
        )
        return self._clamp01(score)

    @staticmethod
    def _notification_score(notification_hint: Optional[dict[str, Any]], class_names: set[str]) -> float:
        if not notification_hint:
            return 0.0
        cls = str(notification_hint.get("top_class") or notification_hint.get("class_name") or "")
        conf = float(notification_hint.get("top_confidence") or notification_hint.get("confidence") or 0.0)
        if cls in class_names:
            return max(0.0, min(1.0, conf))
        # Events may store event name rather than class name.
        ev = str(notification_hint.get("event") or "")
        if ev in class_names:
            return max(0.0, min(1.0, conf))
        return 0.0

    def detect_frame(
        self,
        crops: dict[str, np.ndarray],
        frame_index: int,
        timestamp_sec: float,
        crop_paths: Optional[dict[str, str]] = None,
        notification_hint: Optional[dict[str, Any]] = None,
    ) -> FrameStateReading:
        crop_paths = crop_paths or {}
        notes: list[str] = []
        signals: list[StateSignal] = []
        features_by_roi: dict[str, ImageFeatures] = {}

        for roi_name, crop in crops.items():
            features_by_roi[roi_name] = self.extract_features(crop)

        hp_presence = self._hud_presence_score(features_by_roi.get("hp_ac_area", ImageFeatures(0, 1, 0, 0, 0, 0, 0, 0)))
        weapon_presence = self._hud_presence_score(features_by_roi.get("weapon_ammo_area", ImageFeatures(0, 1, 0, 0, 0, 0, 0, 0)))
        minimap_presence = self._hud_presence_score(features_by_roi.get("minimap", ImageFeatures(0, 1, 0, 0, 0, 0, 0, 0)))
        crosshair_presence = self._crosshair_presence_score(features_by_roi.get("crosshair", ImageFeatures(0, 1, 0, 0, 0, 0, 0, 0)))
        score_bar_presence = self._hud_presence_score(features_by_roi.get("top_score_bar", ImageFeatures(0, 1, 0, 0, 0, 0, 0, 0)))

        for name, value, roi in [
            ("hp_hud_presence", hp_presence, "hp_ac_area"),
            ("weapon_hud_presence", weapon_presence, "weapon_ammo_area"),
            ("minimap_presence", minimap_presence, "minimap"),
            ("crosshair_presence", crosshair_presence, "crosshair"),
            ("score_bar_presence", score_bar_presence, "top_score_bar"),
        ]:
            signals.append(
                StateSignal(
                    name=name,
                    confidence=float(value),
                    status="ok",
                    method="heuristic",
                    roi_name=roi,
                    features=features_by_roi.get(roi),
                )
            )

        template_min = float(self.feature_thresholds.get("template_min_score", 0.60))
        template_scores: dict[str, float] = {}
        for state in [
            "alive_playing",
            "kill_confirmed_overlay",
            "dead_or_killcam",
            "killer_panel",
            "menu_or_loading",
        ]:
            score, match, roi_name = self._best_template_score(crops, state)
            template_scores[state] = score
            if match is not None:
                signals.append(
                    StateSignal(
                        name=f"{state}_template",
                        confidence=float(score),
                        status="ok" if score >= template_min else "low_score",
                        method="template",
                        roi_name=roi_name,
                        template_match=match,
                    )
                )

        kill_hint = self._notification_score(notification_hint, {"kill_feed", "first_kill_medal", "kill_notification"})
        death_hint = self._notification_score(notification_hint, {"death_killer_panel", "death_notification"})
        respawn_hint = self._notification_score(notification_hint, {"respawn_related", "respawn"})

        if notification_hint:
            signals.append(
                StateSignal(
                    name="notification_hint",
                    confidence=float(max(kill_hint, death_hint, respawn_hint)),
                    status="ok" if max(kill_hint, death_hint, respawn_hint) > 0 else "unused",
                    method="notification_report",
                    notes=[str(notification_hint.get("top_class") or notification_hint.get("event") or "")],
                )
            )

        alive_hud_score = self._clamp01(
            0.30 * hp_presence
            + 0.35 * weapon_presence
            + 0.20 * crosshair_presence
            + 0.10 * minimap_presence
            + 0.05 * score_bar_presence
        )

        # A death/killer state suppresses alive confidence strongly.
        killer_score = max(template_scores.get("killer_panel", 0.0), template_scores.get("dead_or_killcam", 0.0) * 0.85, death_hint)
        kill_overlay_score = max(template_scores.get("kill_confirmed_overlay", 0.0), kill_hint)
        dead_score = max(
            template_scores.get("dead_or_killcam", 0.0),
            killer_score * 0.92,
            self._clamp01((1.0 - weapon_presence) * 0.30 + (1.0 - crosshair_presence) * 0.25 + death_hint * 0.45),
        )
        alive_score = max(template_scores.get("alive_playing", 0.0), alive_hud_score * (1.0 - min(0.85, killer_score)))
        respawn_score = max(respawn_hint, alive_score * 0.70 if respawn_hint > 0 else 0.0)
        menu_score = max(
            template_scores.get("menu_or_loading", 0.0),
            self._clamp01((1.0 - hp_presence) * 0.25 + (1.0 - weapon_presence) * 0.25 + (1.0 - minimap_presence) * 0.25 + (1.0 - score_bar_presence) * 0.25)
            if max(hp_presence, weapon_presence, minimap_presence, score_bar_presence) < float(self.feature_thresholds.get("low_hud_presence_max", 0.12))
            else 0.0,
        )

        # Engaging enemy is intentionally weak in this MVP. It needs enemy/reticle/hit-marker models later.
        engaging_score = 0.0

        scores: dict[str, float] = {
            "alive_playing": self._clamp01(alive_score),
            "engaging_enemy": self._clamp01(engaging_score),
            "kill_confirmed_overlay": self._clamp01(kill_overlay_score),
            "dead_or_killcam": self._clamp01(dead_score),
            "killer_panel": self._clamp01(killer_score),
            "respawned_playing": self._clamp01(respawn_score),
            "menu_or_loading": self._clamp01(menu_score),
            "unknown": 0.0,
        }

        # Priority tie-breaker: explicit notification states > alive/menu heuristics.
        priority = [
            "killer_panel",
            "dead_or_killcam",
            "kill_confirmed_overlay",
            "respawned_playing",
            "alive_playing",
            "menu_or_loading",
            "engaging_enemy",
        ]
        best_state = "unknown"
        best_conf = 0.0
        for state in priority:
            conf = scores[state]
            if conf > best_conf:
                best_state = state
                best_conf = conf

        threshold = float(self.state_thresholds.get(best_state, 0.0))
        if best_conf < threshold:
            notes.append(f"best_state_below_threshold:{best_state}:{best_conf:.3f}<{threshold:.3f}")
            best_state = "unknown"
            status = "unknown"
        else:
            status = "ok"

        if best_state == "engaging_enemy":
            notes.append("engaging_enemy is reserved for a future enemy/hit-marker model")

        return FrameStateReading(
            frame_index=int(frame_index),
            timestamp_sec=float(timestamp_sec),
            crop_paths=crop_paths,
            state=best_state,  # type: ignore[arg-type]
            confidence=float(best_conf if best_state != "unknown" else 0.0),
            status=status,
            scores=scores,
            signals=signals,
            notification_hint=notification_hint,
            notes=notes,
        )


class GameStateTemporalAggregator:
    def __init__(
        self,
        state_thresholds: Optional[dict[str, float]] = None,
        smoothing_window_sec: float = 0.8,
        merge_gap_sec: float = 0.8,
        min_segment_duration_sec: float = 0.2,
        respawn_after_death_window_sec: float = 8.0,
        stable_alive_after_respawn_sec: float = 0.5,
    ) -> None:
        self.state_thresholds = state_thresholds or DEFAULT_STATE_THRESHOLDS.copy()
        self.smoothing_window_sec = smoothing_window_sec
        self.merge_gap_sec = merge_gap_sec
        self.min_segment_duration_sec = min_segment_duration_sec
        self.respawn_after_death_window_sec = respawn_after_death_window_sec
        self.stable_alive_after_respawn_sec = stable_alive_after_respawn_sec

    def _smooth_state_at(self, readings: list[FrameStateReading], idx: int) -> tuple[GameStateClass, float]:
        center_t = readings[idx].timestamp_sec
        half = self.smoothing_window_sec / 2.0
        votes: dict[str, float] = {}
        for r in readings:
            if abs(r.timestamp_sec - center_t) > half:
                continue
            # Use the full score vector when present, but discount unknown and low-confidence states.
            for state, score in r.scores.items():
                if state == "unknown":
                    continue
                threshold = float(self.state_thresholds.get(state, 0.0))
                if score < threshold * 0.70:
                    continue
                weight = max(0.0, min(1.0, score))
                votes[state] = votes.get(state, 0.0) + weight

        if not votes:
            return "unknown", 0.0

        priority = {
            "killer_panel": 7,
            "dead_or_killcam": 6,
            "kill_confirmed_overlay": 5,
            "respawned_playing": 4,
            "alive_playing": 3,
            "menu_or_loading": 2,
            "engaging_enemy": 1,
        }
        best_state, best_vote = sorted(votes.items(), key=lambda kv: (kv[1], priority.get(kv[0], 0)), reverse=True)[0]
        total_vote = sum(votes.values())
        confidence = float(best_vote / total_vote) if total_vote > 0 else 0.0
        return best_state, confidence  # type: ignore[return-value]

    def _segments_from_series(self, series: list[dict[str, Any]], readings_by_key: dict[tuple[int, float], FrameStateReading]) -> list[StateSegment]:
        if not series:
            return []
        segments: list[StateSegment] = []
        cur_state = series[0]["state"]
        cur_items = [series[0]]

        def flush(items: list[dict[str, Any]], state: str) -> None:
            if not items:
                return
            start_t = float(items[0]["timestamp_sec"])
            end_t = float(items[-1]["timestamp_sec"])
            if len(items) == 1:
                duration = 0.0
            else:
                duration = max(0.0, end_t - start_t)
            if duration < self.min_segment_duration_sec and len(items) < 2:
                return
            conf = float(np.mean([float(i.get("confidence", 0.0)) for i in items]))
            status = "ok" if state != "unknown" else "unknown"
            segments.append(
                StateSegment(
                    state=state,  # type: ignore[arg-type]
                    start_time=start_t,
                    end_time=end_t,
                    duration_sec=duration,
                    frame_count=len(items),
                    confidence=conf,
                    start_frame_index=int(items[0]["frame_index"]),
                    end_frame_index=int(items[-1]["frame_index"]),
                    status=status,
                )
            )

        for item in series[1:]:
            state = item["state"]
            gap = float(item["timestamp_sec"]) - float(cur_items[-1]["timestamp_sec"])
            if state == cur_state or gap <= self.merge_gap_sec and state == cur_state:
                cur_items.append(item)
            else:
                flush(cur_items, cur_state)
                cur_state = state
                cur_items = [item]
        flush(cur_items, cur_state)
        return segments

    def _transition_events(self, segments: list[StateSegment]) -> list[StateTransitionEvent]:
        events: list[StateTransitionEvent] = []
        for prev, cur in zip(segments, segments[1:]):
            if prev.state == cur.state:
                continue
            source = ["state_transition"]
            conf = float(min(1.0, (prev.confidence + cur.confidence) / 2.0))
            event_name = "state_change"
            status = "INFERRED"
            notes: list[str] = []

            if cur.state in {"killer_panel", "dead_or_killcam"} and prev.state in {"alive_playing", "kill_confirmed_overlay", "respawned_playing", "unknown"}:
                event_name = "death_state_entered"
                status = "CONFIRMED" if cur.state == "killer_panel" and cur.confidence >= 0.60 else "INFERRED"
                source.append(cur.state)
            elif cur.state == "kill_confirmed_overlay":
                event_name = "kill_overlay_entered"
                status = "CONFIRMED" if cur.confidence >= 0.60 else "INFERRED"
                source.append("kill_confirmed_overlay")
            elif prev.state in {"killer_panel", "dead_or_killcam"} and cur.state == "alive_playing":
                event_name = "respawned_playing"
                source.extend(["dead_to_alive_transition", "alive_hud_returned"])
                status = "CONFIRMED" if cur.duration_sec >= self.stable_alive_after_respawn_sec or cur.frame_count >= 2 else "INFERRED"
            elif cur.state == "menu_or_loading":
                event_name = "menu_or_loading_entered"

            events.append(
                StateTransitionEvent(
                    time=cur.start_time,
                    event=event_name,
                    from_state=prev.state,
                    to_state=cur.state,
                    confidence=conf,
                    status=status,
                    source=source,
                    notes=notes,
                )
            )
        return events

    def _respawn_events_from_segments(self, segments: list[StateSegment]) -> list[StateTransitionEvent]:
        extra: list[StateTransitionEvent] = []
        for i, seg in enumerate(segments):
            if seg.state not in {"killer_panel", "dead_or_killcam"}:
                continue
            for later in segments[i + 1:]:
                if later.start_time - seg.end_time > self.respawn_after_death_window_sec:
                    break
                if later.state in {"alive_playing", "respawned_playing"}:
                    status = "CONFIRMED" if later.duration_sec >= self.stable_alive_after_respawn_sec or later.frame_count >= 2 else "INFERRED"
                    extra.append(
                        StateTransitionEvent(
                            time=later.start_time,
                            event="respawn_candidate",
                            from_state=seg.state,
                            to_state="respawned_playing",
                            confidence=float(min(1.0, (seg.confidence + later.confidence) / 2.0)),
                            status=status,
                            source=["death_segment", "alive_hud_returned"],
                            notes=[f"death_time={seg.start_time:.3f}", f"alive_time={later.start_time:.3f}"],
                        )
                    )
                    break
        return extra

    def aggregate(self, readings: list[FrameStateReading]) -> dict[str, Any]:
        readings = sorted(readings, key=lambda r: (r.timestamp_sec, r.frame_index))
        stable_series: list[dict[str, Any]] = []
        readings_by_key: dict[tuple[int, float], FrameStateReading] = {}

        for idx, r in enumerate(readings):
            state, conf = self._smooth_state_at(readings, idx)
            stable_series.append(
                {
                    "frame_index": r.frame_index,
                    "timestamp_sec": r.timestamp_sec,
                    "raw_state": r.state,
                    "raw_confidence": r.confidence,
                    "state": state,
                    "confidence": conf,
                }
            )
            readings_by_key[(r.frame_index, r.timestamp_sec)] = r

        segments = self._segments_from_series(stable_series, readings_by_key)
        events = self._transition_events(segments)
        events.extend(self._respawn_events_from_segments(segments))
        events = sorted(events, key=lambda e: (e.time, e.event))

        return {
            "stable_series": stable_series,
            "segments": [asdict(s) for s in segments],
            "events": [asdict(e) for e in events],
        }


def _asdict_list(items: list[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]


def frame_state_readings_to_dict(readings: list[FrameStateReading]) -> list[dict[str, Any]]:
    return _asdict_list(readings)


def load_state_config(config_path: Optional[str | Path]) -> tuple[dict[str, float], dict[str, Any], dict[str, Any]]:
    cfg = json.loads(json.dumps(DEFAULT_STATE_CONFIG))
    if config_path:
        with Path(config_path).open("r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        for key, value in user_cfg.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg.get("state_thresholds", DEFAULT_STATE_THRESHOLDS), cfg.get("feature_thresholds", {}), cfg.get("temporal", {})


def load_notification_hints(notification_report_path: Optional[str | Path]) -> list[dict[str, Any]]:
    if not notification_report_path:
        return []
    path = Path(notification_report_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        report = json.load(f)

    hints: list[dict[str, Any]] = []
    for r in report.get("frame_readings", []):
        hints.append(
            {
                "frame_index": r.get("frame_index", -1),
                "timestamp_sec": r.get("timestamp_sec", 0.0),
                "top_class": r.get("top_class", "unknown"),
                "top_confidence": r.get("top_confidence", 0.0),
                "source": "notification_frame_reading",
            }
        )
    for e in report.get("temporal_aggregation", {}).get("events", []):
        hints.append(
            {
                "frame_index": e.get("frame_index", -1),
                "timestamp_sec": e.get("time", e.get("start_time", 0.0)),
                "event": e.get("event", ""),
                "class_name": e.get("class_name", "unknown"),
                "confidence": e.get("confidence", 0.0),
                "source": "notification_event",
                "event_window": [e.get("start_time", e.get("time", 0.0)), e.get("end_time", e.get("time", 0.0))],
            }
        )
    return hints


def nearest_notification_hint(
    hints: list[dict[str, Any]],
    frame_index: int,
    timestamp_sec: float,
    max_time_diff_sec: float = 0.35,
) -> Optional[dict[str, Any]]:
    if not hints:
        return None

    # Prefer exact frame-index match when present.
    exact = [h for h in hints if int(h.get("frame_index", -999999)) == int(frame_index)]
    if exact:
        return max(exact, key=lambda h: float(h.get("top_confidence") or h.get("confidence") or 0.0))

    candidates: list[tuple[float, dict[str, Any]]] = []
    for h in hints:
        # Event windows are useful for temporal events.
        if "event_window" in h:
            start, end = h["event_window"]
            start = float(start)
            end = float(end)
            if start <= timestamp_sec <= end:
                return h
        dt = abs(float(h.get("timestamp_sec", 0.0)) - timestamp_sec)
        if dt <= max_time_diff_sec:
            candidates.append((dt, h))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -float(item[1].get("top_confidence") or item[1].get("confidence") or 0.0)))
    return candidates[0][1]
