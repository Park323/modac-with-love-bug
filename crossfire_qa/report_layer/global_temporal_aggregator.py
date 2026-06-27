"""
detector report들을 하나로 합쳐서 global_event_timeline.json을 만듦

1. 각 report의 event를 raw event로 정규화
2. 시간순 정렬
3. 가까운 중복 event de-duplication
4. count_change ↔ kill_notification 연결
5. death_notification ↔ respawn_segment ↔ spawn_location_check 연결
6. global event confidence 재계산
7. unmatched/supporting event 분리

kill_confidence =
  0.45 * kill_notification_confidence
+ 0.40 * count_change_confidence
+ 0.15 * game_state_confidence

death_respawn_confidence =
  0.50 * respawn_segment_confidence
+ 0.25 * death_notification_confidence
+ 0.15 * game_state_confidence
+ 0.10 * spawn_location_confidence

"""


from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


DEFAULT_GLOBAL_TEMPORAL_CONFIG: dict[str, Any] = {
    "windows": {
        "dedup_window_sec": 0.60,
        "kill_match_window_sec": 2.00,
        "death_match_window_sec": 1.50,
        "respawn_match_window_sec": 2.00,
        "spawn_match_window_sec": 2.00,
        "nearby_state_window_sec": 1.00,
    },
    "confidence_weights": {
        "kill": {
            "kill_notification": 0.45,
            "count_change": 0.40,
            "game_state": 0.15,
        },
        "death_respawn": {
            "respawn_segment": 0.50,
            "death_notification": 0.25,
            "game_state": 0.15,
            "spawn_location": 0.10,
        },
        "death_only": {
            "death_notification": 0.55,
            "game_state": 0.45,
        },
    },
    "thresholds": {
        "confirmed_confidence": 0.75,
        "inferred_confidence": 0.50,
        "need_review_conflict_confidence": 0.65,
        "standalone_min_confidence": 0.35,
    },
    "status_rank": {
        "CONFIRMED": 5,
        "PASS": 5,
        "INFERRED": 4,
        "OBSERVED": 3,
        "UNCERTAIN": 2,
        "NEED_REVIEW": 2,
        "CONFLICT": 1,
        "FAIL": 1,
        "MISSING": 1,
        "UNKNOWN": 0,
    },
}


@dataclass
class RawTimelineEvent:
    raw_id: str
    source_module: str
    event: str
    event_type: str
    time: float
    end_time: Optional[float]
    confidence: float
    status: str
    source: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class GlobalEvent:
    event_id: str
    event_type: str
    time: float
    end_time: Optional[float]
    confidence: float
    status: str
    source: list[str]
    linked_raw_event_ids: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class GlobalTimelineSummary:
    num_raw_events: int
    num_global_events: int
    num_kill_events: int
    num_death_respawn_events: int
    num_death_only_events: int
    num_spawn_location_events: int
    num_unmatched_events: int
    first_event_time: Optional[float]
    last_event_time: Optional[float]
    overall_confidence_mean: float


class GlobalTemporalAggregator:
    """
    Merge module-level reports into a global event timeline.

    Inputs:
      - kill_count_report.json
      - notification_report.json
      - game_state_report.json
      - respawn_segment_report.json
      - spawn_location_report.json

    Output:
      - raw_events: normalized lower-level detector events
      - global_events: deduplicated, linked semantic events such as kill and death_respawn

    This class intentionally does not perform final QA PASS/FAIL rule evaluation.
    It prepares clean, linked events for the QA Rule Engine.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        cfg = json.loads(json.dumps(DEFAULT_GLOBAL_TEMPORAL_CONFIG))
        if config:
            for key, value in config.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
        self.config = cfg
        self.windows = cfg.get("windows", {})
        self.weights = cfg.get("confidence_weights", {})
        self.thresholds = cfg.get("thresholds", {})
        self.status_rank = cfg.get("status_rank", {})

    @staticmethod
    def load_json(path: Optional[str | Path]) -> Optional[dict[str, Any]]:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Report not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _clamp01(value: Any) -> float:
        try:
            v = float(value)
        except Exception:
            return 0.0
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return float(max(0.0, min(1.0, v)))

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            v = float(value)
        except Exception:
            return default
        if math.isnan(v) or math.isinf(v):
            return default
        return v

    @staticmethod
    def _first_present(item: dict[str, Any], keys: list[str], default: Any = None) -> Any:
        for key in keys:
            if key in item and item[key] is not None:
                return item[key]
        return default

    def _event_time(self, item: dict[str, Any], default: float = 0.0) -> float:
        return self._safe_float(
            self._first_present(item, ["time", "timestamp_sec", "start_time", "death_time", "respawn_time"], default),
            default,
        )

    def _event_end_time(self, item: dict[str, Any]) -> Optional[float]:
        value = self._first_present(item, ["end_time", "respawn_time"], None)
        if value is None:
            return None
        return self._safe_float(value, 0.0)

    def _confidence(self, item: dict[str, Any], default: float = 0.0) -> float:
        return self._clamp01(self._first_present(item, ["confidence", "final_spawn_score", "top_confidence"], default))

    def _status(self, item: dict[str, Any]) -> str:
        status = str(item.get("status", item.get("result", "UNKNOWN")) or "UNKNOWN")
        return status.upper()

    def _source(self, item: dict[str, Any]) -> list[str]:
        source = item.get("source", [])
        if isinstance(source, str):
            return [source]
        if isinstance(source, list):
            return [str(s) for s in source]
        return []

    def _make_raw(
        self,
        raw_id: str,
        source_module: str,
        event: str,
        event_type: str,
        item: dict[str, Any],
        time: Optional[float] = None,
        end_time: Optional[float] = None,
        confidence: Optional[float] = None,
        status: Optional[str] = None,
        notes: Optional[list[str]] = None,
    ) -> RawTimelineEvent:
        return RawTimelineEvent(
            raw_id=raw_id,
            source_module=source_module,
            event=event,
            event_type=event_type,
            time=float(self._event_time(item) if time is None else time),
            end_time=self._event_end_time(item) if end_time is None else end_time,
            confidence=self._confidence(item) if confidence is None else self._clamp01(confidence),
            status=self._status(item) if status is None else status.upper(),
            source=self._source(item),
            payload=item,
            notes=notes or list(item.get("notes", []) or []),
        )

    def extract_raw_events(
        self,
        kill_count_report: Optional[dict[str, Any]] = None,
        notification_report: Optional[dict[str, Any]] = None,
        game_state_report: Optional[dict[str, Any]] = None,
        respawn_report: Optional[dict[str, Any]] = None,
        spawn_location_report: Optional[dict[str, Any]] = None,
    ) -> list[RawTimelineEvent]:
        raw: list[RawTimelineEvent] = []
        idx = 1

        def next_id(prefix: str) -> str:
            nonlocal idx
            rid = f"raw_{idx:05d}_{prefix}"
            idx += 1
            return rid

        if kill_count_report:
            for ev in kill_count_report.get("temporal_aggregation", {}).get("events", []) or []:
                raw.append(
                    self._make_raw(
                        next_id("count_change"),
                        "kill_count_reader",
                        str(ev.get("event", "count_change")),
                        "count_change",
                        ev,
                    )
                )

        if notification_report:
            for ev in notification_report.get("temporal_aggregation", {}).get("events", []) or []:
                event_name = str(ev.get("event", "notification"))
                cls = str(ev.get("class_name", ""))
                if event_name == "kill_notification" or cls in {"kill_feed", "first_kill_medal"}:
                    event_type = "kill_notification"
                elif event_name == "death_notification" or cls == "death_killer_panel":
                    event_type = "death_notification"
                elif event_name == "respawn_related" or cls == "respawn_related":
                    event_type = "respawn_related"
                else:
                    event_type = "notification"
                raw.append(
                    self._make_raw(
                        next_id(event_type),
                        "notification_detector",
                        event_name,
                        event_type,
                        ev,
                    )
                )

        if game_state_report:
            agg = game_state_report.get("temporal_aggregation", {})
            for ev in agg.get("events", []) or []:
                event_name = str(ev.get("event", "game_state_event"))
                state = str(ev.get("to_state", ev.get("state", "")))
                if event_name in {"death_state_entered"} or state in {"killer_panel", "dead_or_killcam"}:
                    event_type = "death_state"
                elif event_name in {"respawn_candidate"} or state in {"alive_playing", "respawned_playing"}:
                    event_type = "alive_state"
                elif event_name in {"kill_overlay_entered"} or state == "kill_confirmed_overlay":
                    event_type = "kill_state"
                else:
                    event_type = "game_state_event"
                raw.append(
                    self._make_raw(
                        next_id(event_type),
                        "game_state_classifier",
                        event_name,
                        event_type,
                        ev,
                    )
                )
            # Segments are useful evidence for final reports, but too verbose as primary events.
            # Keep death/alive segments as raw evidence events only when they are semantically important.
            for seg in agg.get("segments", []) or []:
                state = str(seg.get("state", "unknown"))
                if state not in {"killer_panel", "dead_or_killcam", "alive_playing", "respawned_playing", "kill_confirmed_overlay"}:
                    continue
                if state in {"killer_panel", "dead_or_killcam"}:
                    event_type = "death_state_segment"
                elif state in {"alive_playing", "respawned_playing"}:
                    event_type = "alive_state_segment"
                else:
                    event_type = "kill_state_segment"
                raw.append(
                    self._make_raw(
                        next_id(event_type),
                        "game_state_classifier",
                        f"segment:{state}",
                        event_type,
                        seg,
                        time=self._safe_float(seg.get("start_time", 0.0)),
                        end_time=self._safe_float(seg.get("end_time", seg.get("start_time", 0.0))),
                        confidence=self._confidence(seg),
                        status=str(seg.get("status", "OBSERVED")).upper(),
                    )
                )

        if respawn_report:
            rd = respawn_report.get("respawn_detection", respawn_report)
            for ev in rd.get("respawn_events", []) or []:
                death_time = self._safe_float(ev.get("death_time", 0.0))
                respawn_time = ev.get("respawn_time")
                event_type = "respawn_segment" if respawn_time is not None else "missing_respawn"
                raw.append(
                    self._make_raw(
                        next_id(event_type),
                        "respawn_segment_detector",
                        str(ev.get("event", "respawn_segment")),
                        event_type,
                        ev,
                        time=death_time,
                        end_time=self._safe_float(respawn_time, death_time) if respawn_time is not None else None,
                    )
                )
            for seg in rd.get("death_segments", []) or []:
                raw.append(
                    self._make_raw(
                        next_id("death_segment"),
                        "respawn_segment_detector",
                        "death_segment",
                        "death_segment",
                        seg,
                        time=self._safe_float(seg.get("start_time", 0.0)),
                        end_time=self._safe_float(seg.get("end_time", seg.get("start_time", 0.0))),
                    )
                )

        if spawn_location_report:
            for ev in spawn_location_report.get("spawn_location_events", []) or []:
                raw.append(
                    self._make_raw(
                        next_id("spawn_location"),
                        "spawn_location_recognizer",
                        str(ev.get("event", "spawn_location_check")),
                        "spawn_location_check",
                        ev,
                        time=self._safe_float(ev.get("respawn_time", ev.get("time", 0.0))),
                        end_time=None,
                        confidence=self._confidence(ev),
                        status=str(ev.get("status", ev.get("result", "UNKNOWN"))).upper(),
                    )
                )

        raw.sort(key=lambda e: (e.time, e.event_type, e.raw_id))
        return raw

    def deduplicate_raw_events(self, raw_events: list[RawTimelineEvent]) -> list[RawTimelineEvent]:
        """Merge near-duplicate raw events of the same type from the same detector."""
        if not raw_events:
            return []
        window = float(self.windows.get("dedup_window_sec", 0.6))
        events = sorted(raw_events, key=lambda e: (e.source_module, e.event_type, e.time))
        clusters: list[list[RawTimelineEvent]] = []
        for ev in events:
            if not clusters:
                clusters.append([ev])
                continue
            last_cluster = clusters[-1]
            anchor = last_cluster[-1]
            if ev.source_module == anchor.source_module and ev.event_type == anchor.event_type and abs(ev.time - anchor.time) <= window:
                last_cluster.append(ev)
            else:
                clusters.append([ev])

        merged: list[RawTimelineEvent] = []
        for cluster in clusters:
            if len(cluster) == 1:
                merged.append(cluster[0])
                continue
            best = max(cluster, key=lambda e: (e.confidence, -abs(e.time - cluster[0].time)))
            all_ids = [e.raw_id for e in cluster]
            all_sources = sorted(set(s for e in cluster for s in e.source))
            all_notes = sorted(set(n for e in cluster for n in e.notes))
            payload = dict(best.payload)
            payload["deduped_raw_event_ids"] = all_ids
            payload["dedup_count"] = len(cluster)
            merged.append(
                RawTimelineEvent(
                    raw_id=best.raw_id,
                    source_module=best.source_module,
                    event=best.event,
                    event_type=best.event_type,
                    time=float(sum(e.time for e in cluster) / len(cluster)),
                    end_time=max((e.end_time or e.time) for e in cluster),
                    confidence=max(e.confidence for e in cluster),
                    status=self._best_status([e.status for e in cluster]),
                    source=all_sources,
                    payload=payload,
                    notes=all_notes + [f"deduplicated:{len(cluster)}"],
                )
            )
        merged.sort(key=lambda e: (e.time, e.event_type, e.raw_id))
        return merged

    def _best_status(self, statuses: list[str]) -> str:
        if not statuses:
            return "UNKNOWN"
        return max(statuses, key=lambda s: self.status_rank.get(str(s).upper(), 0)).upper()

    def _weighted_confidence(self, parts: dict[str, Optional[float]], group: str) -> float:
        weights = self.weights.get(group, {})
        total = 0.0
        denom = 0.0
        for key, value in parts.items():
            if value is None:
                continue
            weight = float(weights.get(key, 0.0))
            if weight <= 0:
                continue
            total += weight * self._clamp01(value)
            denom += weight
        return self._clamp01(total / denom) if denom > 0 else 0.0

    def _status_from_confidence(self, confidence: float, required_parts: int, conflict: bool = False) -> str:
        if conflict:
            return "NEED_REVIEW"
        confirmed = float(self.thresholds.get("confirmed_confidence", 0.75))
        inferred = float(self.thresholds.get("inferred_confidence", 0.50))
        if confidence >= confirmed and required_parts >= 2:
            return "CONFIRMED"
        if confidence >= inferred:
            return "INFERRED"
        return "UNCERTAIN"

    @staticmethod
    def _nearest(
        events: list[RawTimelineEvent],
        target_time: float,
        window: float,
        used: Optional[set[str]] = None,
    ) -> Optional[RawTimelineEvent]:
        candidates: list[tuple[float, float, RawTimelineEvent]] = []
        used = used or set()
        for ev in events:
            if ev.raw_id in used:
                continue
            d = abs(ev.time - target_time)
            if d <= window:
                candidates.append((d, -ev.confidence, ev))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][2]

    @staticmethod
    def _nearest_count_for_kill(
        count_events: list[RawTimelineEvent],
        target_time: float,
        window: float,
        used: set[str],
        expected_score_side: Optional[str],
    ) -> tuple[Optional[RawTimelineEvent], list[str]]:
        notes: list[str] = []
        if expected_score_side:
            same_side = [
                e for e in count_events
                if str(e.payload.get("side", "")) == str(expected_score_side)
            ]
            matched = GlobalTemporalAggregator._nearest(same_side, target_time, window, used)
            if matched is not None:
                notes.append(f"kill_feed_expected_score_side:{expected_score_side}")
                return matched, notes
            notes.append(f"no_count_change_for_expected_score_side:{expected_score_side}")

        matched = GlobalTemporalAggregator._nearest(count_events, target_time, window, used)
        if matched is not None and expected_score_side:
            notes.append(f"fallback_count_change_side:{matched.payload.get('side')}")
        return matched, notes

    @staticmethod
    def _events_of(raw: list[RawTimelineEvent], types: set[str]) -> list[RawTimelineEvent]:
        return [e for e in raw if e.event_type in types]

    def _make_global_event(
        self,
        event_id: str,
        event_type: str,
        time: float,
        linked: list[RawTimelineEvent],
        confidence: float,
        status: str,
        evidence: Optional[dict[str, Any]] = None,
        end_time: Optional[float] = None,
        notes: Optional[list[str]] = None,
    ) -> GlobalEvent:
        source = sorted(set([e.source_module for e in linked] + [s for e in linked for s in e.source]))
        linked_ids = [e.raw_id for e in linked]
        merged_notes = sorted(set((notes or []) + [n for e in linked for n in e.notes]))
        return GlobalEvent(
            event_id=event_id,
            event_type=event_type,
            time=float(time),
            end_time=end_time,
            confidence=self._clamp01(confidence),
            status=status.upper(),
            source=source,
            linked_raw_event_ids=linked_ids,
            evidence=evidence or {},
            notes=merged_notes,
        )

    def build_global_events(self, raw_events: list[RawTimelineEvent]) -> tuple[list[GlobalEvent], list[RawTimelineEvent]]:
        raw = self.deduplicate_raw_events(raw_events)
        used: set[str] = set()
        global_events: list[GlobalEvent] = []
        next_idx = 1

        def next_event_id() -> str:
            nonlocal next_idx
            eid = f"evt_{next_idx:05d}"
            next_idx += 1
            return eid

        kill_notifs = self._events_of(raw, {"kill_notification"})
        count_changes = self._events_of(raw, {"count_change"})
        kill_states = self._events_of(raw, {"kill_state", "kill_state_segment"})
        kill_window = float(self.windows.get("kill_match_window_sec", 2.0))
        state_window = float(self.windows.get("nearby_state_window_sec", 1.0))

        # Anchor kill events on notifications first, then attach score changes.
        for notif in kill_notifs:
            if notif.raw_id in used:
                continue
            expected_score_side = notif.payload.get("expected_score_side")
            count, side_notes = self._nearest_count_for_kill(count_changes, notif.time, kill_window, used, expected_score_side)
            if count is None and "kill_feed" in notif.source:
                # Heuristic kill-feed candidates are noisy. The current QA policy
                # validates kills from kill feed plus the corresponding team score
                # increment, so feed-only candidates stay as unmatched/supporting
                # raw events instead of becoming failing kill checks.
                continue
            state = self._nearest(kill_states, notif.time, state_window, used)
            linked = [notif]
            if count:
                linked.append(count)
            if state:
                linked.append(state)

            part_conf = {
                "kill_notification": notif.confidence,
                "count_change": count.confidence if count else None,
                "game_state": state.confidence if state else None,
            }
            conf = self._weighted_confidence(part_conf, "kill")
            required_parts = 1 + int(count is not None) + int(state is not None)
            status = self._status_from_confidence(conf, required_parts)
            notes: list[str] = list(side_notes)
            if count is None:
                notes.append("kill_notification_without_count_change_match")
            evidence = {
                "kill_notification": asdict(notif),
                "count_change": asdict(count) if count else None,
                "game_state": asdict(state) if state else None,
                "expected_score_side": expected_score_side,
                "observed_score_side": count.payload.get("side") if count else None,
                "time_delta_sec": abs(notif.time - count.time) if count else None,
            }
            ev_time = min(e.time for e in linked)
            for e in linked:
                used.add(e.raw_id)
            global_events.append(self._make_global_event(next_event_id(), "kill", ev_time, linked, conf, status, evidence, notes=notes))

        # Count changes without notification become possible kill/count events.
        for count in count_changes:
            if count.raw_id in used:
                continue
            state = self._nearest(kill_states, count.time, state_window, used)
            linked = [count] + ([state] if state else [])
            matched_feed = count.payload.get("matched_kill_feed") if isinstance(count.payload.get("matched_kill_feed"), dict) else None
            part_conf = {
                "kill_notification": self._confidence(matched_feed) if matched_feed else None,
                "count_change": count.confidence,
                "game_state": state.confidence if state else None,
            }
            conf = self._weighted_confidence(part_conf, "kill")
            if matched_feed:
                status = self._status_from_confidence(conf, 2 + int(state is not None))
                event_type = "kill"
                notes = ["kill_feed_score_side_crosscheck"]
            else:
                status = "INFERRED" if conf >= float(self.thresholds.get("inferred_confidence", 0.5)) else "UNCERTAIN"
                event_type = "kill_candidate"
                notes = ["count_change_without_kill_notification_match"]
            evidence = {
                "kill_notification": {"payload": matched_feed, "confidence": self._confidence(matched_feed)} if matched_feed else None,
                "count_change": asdict(count),
                "game_state": asdict(state) if state else None,
                "expected_score_side": (matched_feed or {}).get("expected_score_side"),
                "observed_score_side": count.payload.get("side"),
            }
            for e in linked:
                used.add(e.raw_id)
            global_events.append(self._make_global_event(next_event_id(), event_type, count.time, linked, conf, status, evidence, notes=notes))

        # Respawn/death events are anchored on respawn segments.
        death_notifs = self._events_of(raw, {"death_notification"})
        death_states = self._events_of(raw, {"death_state", "death_state_segment", "death_segment"})
        respawn_segments = self._events_of(raw, {"respawn_segment", "missing_respawn"})
        spawn_checks = self._events_of(raw, {"spawn_location_check"})
        death_window = float(self.windows.get("death_match_window_sec", 1.5))
        spawn_window = float(self.windows.get("spawn_match_window_sec", 2.0))

        for resp in respawn_segments:
            if resp.raw_id in used:
                continue
            death_time = self._safe_float(resp.payload.get("death_time", resp.time), resp.time)
            respawn_time = resp.payload.get("respawn_time")
            respawn_time_f = self._safe_float(respawn_time, resp.end_time or resp.time) if respawn_time is not None else None
            death_notif = self._nearest(death_notifs, death_time, death_window, used)
            death_state = self._nearest(death_states, death_time, death_window, used)
            spawn = self._nearest(spawn_checks, respawn_time_f, spawn_window, used) if respawn_time_f is not None else None
            linked = [resp]
            for item in [death_notif, death_state, spawn]:
                if item is not None:
                    linked.append(item)

            spawn_conf: Optional[float] = None
            spawn_result = None
            if spawn:
                spawn_result = str(spawn.payload.get("result", spawn.status)).upper()
                if spawn_result == "PASS":
                    spawn_conf = spawn.confidence
                elif spawn_result == "FAIL":
                    spawn_conf = 0.0
                elif spawn_result in {"UNCERTAIN", "OBSERVED"}:
                    spawn_conf = max(0.0, spawn.confidence * 0.5)
                else:
                    spawn_conf = spawn.confidence

            part_conf = {
                "respawn_segment": resp.confidence,
                "death_notification": death_notif.confidence if death_notif else None,
                "game_state": death_state.confidence if death_state else None,
                "spawn_location": spawn_conf,
            }
            conf = self._weighted_confidence(part_conf, "death_respawn")
            required_parts = 1 + int(death_notif is not None) + int(death_state is not None) + int(spawn is not None)
            conflict = spawn_result == "FAIL"
            if resp.event_type == "missing_respawn" or str(resp.payload.get("result", "")).upper() == "RESPAWN_MISSING":
                status = "MISSING"
                event_type = "death_missing_respawn"
                notes = ["death_detected_but_respawn_missing"]
            else:
                status = self._status_from_confidence(conf, required_parts, conflict=conflict)
                event_type = "death_respawn"
                notes = []
                if spawn is None:
                    notes.append("respawn_without_spawn_location_check")
                elif spawn_result == "FAIL":
                    notes.append("spawn_location_check_failed")
                elif spawn_result == "UNCERTAIN":
                    notes.append("spawn_location_check_uncertain")

            evidence = {
                "respawn_segment": asdict(resp),
                "death_notification": asdict(death_notif) if death_notif else None,
                "death_state": asdict(death_state) if death_state else None,
                "spawn_location_check": asdict(spawn) if spawn else None,
                "death_time": death_time,
                "respawn_time": respawn_time_f,
            }
            for e in linked:
                used.add(e.raw_id)
            global_events.append(
                self._make_global_event(
                    next_event_id(),
                    event_type,
                    death_time,
                    linked,
                    conf,
                    status,
                    evidence,
                    end_time=respawn_time_f,
                    notes=notes,
                )
            )

        # Death notifications not captured by a respawn segment become death_only candidates.
        for death in death_notifs:
            if death.raw_id in used:
                continue
            state = self._nearest(death_states, death.time, death_window, used)
            linked = [death] + ([state] if state else [])
            conf = self._weighted_confidence(
                {"death_notification": death.confidence, "game_state": state.confidence if state else None},
                "death_only",
            )
            status = "INFERRED" if conf >= float(self.thresholds.get("inferred_confidence", 0.5)) else "UNCERTAIN"
            evidence = {"death_notification": asdict(death), "death_state": asdict(state) if state else None}
            for e in linked:
                used.add(e.raw_id)
            global_events.append(
                self._make_global_event(next_event_id(), "death_only", death.time, linked, conf, status, evidence, notes=["death_without_respawn_segment_match"])
            )

        # Standalone spawn checks can be kept for observed/debug cases.
        for spawn in spawn_checks:
            if spawn.raw_id in used:
                continue
            evidence = {"spawn_location_check": asdict(spawn)}
            event_type = "spawn_location_observed" if str(spawn.payload.get("result", "")).upper() == "OBSERVED" else "spawn_location_check"
            for e in [spawn]:
                used.add(e.raw_id)
            global_events.append(
                self._make_global_event(next_event_id(), event_type, spawn.time, [spawn], spawn.confidence, spawn.status, evidence, notes=["standalone_spawn_location_event"])
            )

        # Keep high-confidence state events not linked above as supporting timeline entries.
        standalone_min = float(self.thresholds.get("standalone_min_confidence", 0.35))
        for ev in raw:
            if ev.raw_id in used:
                continue
            if ev.event_type in {"alive_state_segment", "game_state_event"} and ev.confidence < standalone_min:
                continue
            if ev.event_type in {"death_state", "death_state_segment", "alive_state", "kill_state", "respawn_related"}:
                global_events.append(
                    self._make_global_event(
                        next_event_id(),
                        f"supporting_{ev.event_type}",
                        ev.time,
                        [ev],
                        ev.confidence,
                        ev.status,
                        {"raw_event": asdict(ev)},
                        end_time=ev.end_time,
                        notes=["supporting_event_not_linked"],
                    )
                )
                used.add(ev.raw_id)

        global_events.sort(key=lambda e: (e.time, e.event_type, e.event_id))
        unmatched = [e for e in raw if e.raw_id not in used]
        return global_events, unmatched

    def aggregate(
        self,
        kill_count_report: Optional[dict[str, Any]] = None,
        notification_report: Optional[dict[str, Any]] = None,
        game_state_report: Optional[dict[str, Any]] = None,
        respawn_report: Optional[dict[str, Any]] = None,
        spawn_location_report: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        raw = self.extract_raw_events(
            kill_count_report=kill_count_report,
            notification_report=notification_report,
            game_state_report=game_state_report,
            respawn_report=respawn_report,
            spawn_location_report=spawn_location_report,
        )
        deduped = self.deduplicate_raw_events(raw)
        global_events, unmatched = self.build_global_events(deduped)
        summary = self._summary(raw, global_events, unmatched)
        return {
            "config": self.config,
            "summary": asdict(summary),
            "raw_events": [asdict(e) for e in raw],
            "deduped_raw_events": [asdict(e) for e in deduped],
            "global_events": [asdict(e) for e in global_events],
            "unmatched_events": [asdict(e) for e in unmatched],
        }

    def _summary(
        self,
        raw_events: list[RawTimelineEvent],
        global_events: list[GlobalEvent],
        unmatched: list[RawTimelineEvent],
    ) -> GlobalTimelineSummary:
        times = [e.time for e in global_events]
        confs = [e.confidence for e in global_events]
        return GlobalTimelineSummary(
            num_raw_events=len(raw_events),
            num_global_events=len(global_events),
            num_kill_events=sum(1 for e in global_events if e.event_type in {"kill", "kill_candidate"}),
            num_death_respawn_events=sum(1 for e in global_events if e.event_type in {"death_respawn", "death_missing_respawn"}),
            num_death_only_events=sum(1 for e in global_events if e.event_type == "death_only"),
            num_spawn_location_events=sum(1 for e in global_events if e.event_type in {"spawn_location_check", "spawn_location_observed"}),
            num_unmatched_events=len(unmatched),
            first_event_time=min(times) if times else None,
            last_event_time=max(times) if times else None,
            overall_confidence_mean=float(sum(confs) / len(confs)) if confs else 0.0,
        )


def load_global_temporal_config(config_path: Optional[str | Path]) -> dict[str, Any]:
    if not config_path:
        return json.loads(json.dumps(DEFAULT_GLOBAL_TEMPORAL_CONFIG))
    with Path(config_path).open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_GLOBAL_TEMPORAL_CONFIG))
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged
