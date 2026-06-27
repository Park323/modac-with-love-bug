from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


DEFAULT_QA_RULE_CONFIG: dict[str, Any] = {
    "thresholds": {
        "pass_confidence": 0.75,
        "inferred_confidence": 0.50,
        "min_signal_confidence": 0.45,
        "max_kill_count_delay_sec": 2.0,
        "spawn_pass_threshold": 0.75,
        "spawn_fail_threshold": 0.45,
    },
    "rules": {
        "require_count_change_after_kill_notification": True,
        "require_kill_notification_for_count_change": False,
        "require_death_notification": True,
        "death_notification_optional_if_absent": True,
        "require_spawn_location_check": False,
        "require_respawn_after_death": True,
        "fail_on_missing_respawn": True,
        "fail_on_death_without_respawn_segment": False,
    },
    "result_priority": {
        "PASS": 0,
        "UNCERTAIN": 1,
        "NEED_REVIEW": 2,
        "FAIL": 3,
    },
}


@dataclass
class RuleFinding:
    rule_id: str
    objective: str
    target_event_id: Optional[str]
    target_event_type: Optional[str]
    time: Optional[float]
    result: str
    confidence: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class ObjectiveResult:
    rule_id: str
    objective: str
    result: str
    confidence: float
    pass_count: int
    fail_count: int
    uncertain_count: int
    need_review_count: int
    findings: list[RuleFinding]
    summary: str


@dataclass
class QASummary:
    overall_result: str
    overall_confidence: float
    num_objectives: int
    num_pass: int
    num_fail: int
    num_uncertain: int
    num_need_review: int
    num_findings: int


class QARuleEngine:
    """
    Final rule engine for CrossFire QA.

    Input:
      - global_event_timeline.json from GlobalTemporalAggregator

    Output:
      - qa_rule_report.json with objective-level PASS / FAIL / UNCERTAIN / NEED_REVIEW

    This layer does not re-run detectors. It evaluates the linked global timeline.
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        cfg = json.loads(json.dumps(DEFAULT_QA_RULE_CONFIG))
        if config:
            for key, value in config.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
        self.config = cfg
        self.thresholds = cfg.get("thresholds", {})
        self.rules = cfg.get("rules", {})
        self.result_priority = cfg.get("result_priority", DEFAULT_QA_RULE_CONFIG["result_priority"])

    @staticmethod
    def load_json(path: str | Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            v = float(value)
        except Exception:
            return default
        if math.isnan(v) or math.isinf(v):
            return default
        return v

    @classmethod
    def _clamp01(cls, value: Any) -> float:
        return max(0.0, min(1.0, cls._safe_float(value, 0.0)))

    def _event_conf(self, ev: dict[str, Any]) -> float:
        return self._clamp01(ev.get("confidence", 0.0))

    def _signal_conf(self, signal: Optional[dict[str, Any]], default: float = 0.0) -> float:
        if not isinstance(signal, dict):
            return default
        if "confidence" in signal:
            return self._clamp01(signal.get("confidence", default))
        payload = signal.get("payload")
        if isinstance(payload, dict):
            return self._clamp01(payload.get("confidence", payload.get("final_spawn_score", default)))
        return default

    def _signal_payload(self, signal: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(signal, dict):
            return {}
        payload = signal.get("payload")
        if isinstance(payload, dict):
            return payload
        return signal

    def _has_signal(self, signal: Optional[dict[str, Any]], min_conf: Optional[float] = None) -> bool:
        if not isinstance(signal, dict):
            return False
        if min_conf is None:
            min_conf = float(self.thresholds.get("min_signal_confidence", 0.45))
        return self._signal_conf(signal) >= min_conf

    @staticmethod
    def _ev_time(ev: dict[str, Any]) -> Optional[float]:
        value = ev.get("time")
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    def _make_finding(
        self,
        rule_id: str,
        objective: str,
        ev: Optional[dict[str, Any]],
        result: str,
        confidence: float,
        reason: str,
        evidence: Optional[dict[str, Any]] = None,
        notes: Optional[list[str]] = None,
    ) -> RuleFinding:
        return RuleFinding(
            rule_id=rule_id,
            objective=objective,
            target_event_id=ev.get("event_id") if isinstance(ev, dict) else None,
            target_event_type=ev.get("event_type") if isinstance(ev, dict) else None,
            time=self._ev_time(ev) if isinstance(ev, dict) else None,
            result=result.upper(),
            confidence=self._clamp01(confidence),
            reason=reason,
            evidence=evidence or {},
            notes=notes or [],
        )

    def _objective_result(self, rule_id: str, objective: str, findings: list[RuleFinding]) -> ObjectiveResult:
        if not findings:
            findings = [
                RuleFinding(
                    rule_id=rule_id,
                    objective=objective,
                    target_event_id=None,
                    target_event_type=None,
                    time=None,
                    result="UNCERTAIN",
                    confidence=0.0,
                    reason="No finding was produced for this objective.",
                )
            ]

        counts = {"PASS": 0, "FAIL": 0, "UNCERTAIN": 0, "NEED_REVIEW": 0}
        for f in findings:
            counts[f.result] = counts.get(f.result, 0) + 1

        # Conservative aggregation: explicit FAIL wins, then NEED_REVIEW, then UNCERTAIN.
        if counts.get("FAIL", 0) > 0:
            result = "FAIL"
            confidence = max(f.confidence for f in findings if f.result == "FAIL")
            summary = f"{counts['FAIL']} failing finding(s) detected."
        elif counts.get("NEED_REVIEW", 0) > 0:
            result = "NEED_REVIEW"
            confidence = max(f.confidence for f in findings if f.result == "NEED_REVIEW")
            summary = f"{counts['NEED_REVIEW']} finding(s) require review."
        elif counts.get("UNCERTAIN", 0) > 0:
            result = "UNCERTAIN"
            uncertain = [f.confidence for f in findings if f.result == "UNCERTAIN"]
            confidence = float(sum(uncertain) / len(uncertain)) if uncertain else 0.0
            summary = f"{counts['UNCERTAIN']} uncertain finding(s); evidence is insufficient for full PASS."
        else:
            result = "PASS"
            # Use min confidence for PASS because all checks must hold.
            confidence = min(f.confidence for f in findings) if findings else 0.0
            summary = "All evaluated findings passed."

        return ObjectiveResult(
            rule_id=rule_id,
            objective=objective,
            result=result,
            confidence=self._clamp01(confidence),
            pass_count=counts.get("PASS", 0),
            fail_count=counts.get("FAIL", 0),
            uncertain_count=counts.get("UNCERTAIN", 0),
            need_review_count=counts.get("NEED_REVIEW", 0),
            findings=findings,
            summary=summary,
        )

    def _global_events(self, global_timeline: dict[str, Any]) -> list[dict[str, Any]]:
        events = global_timeline.get("global_events", [])
        if not isinstance(events, list):
            return []
        return [e for e in events if isinstance(e, dict)]

    def evaluate_kill_count_rule(self, global_timeline: dict[str, Any]) -> ObjectiveResult:
        rule_id = "kill_count_increment"
        objective = "Kill count should change correctly after a confirmed kill."
        if (
            not self.rules.get("require_count_change_after_kill_notification", True)
            and not self.rules.get("require_kill_notification_for_count_change", True)
        ):
            return self._objective_result(rule_id, objective, [
                self._make_finding(
                    rule_id,
                    objective,
                    None,
                    "PASS",
                    1.0,
                    "Kill count cross-check is disabled by QA config.",
                    notes=["rule_disabled"],
                )
            ])
        events = [e for e in self._global_events(global_timeline) if e.get("event_type") in {"kill", "kill_candidate"}]
        min_signal = float(self.thresholds.get("min_signal_confidence", 0.45))
        max_delay = float(self.thresholds.get("max_kill_count_delay_sec", 2.0))
        findings: list[RuleFinding] = []

        if not events:
            findings.append(self._make_finding(
                rule_id,
                objective,
                None,
                "UNCERTAIN",
                0.0,
                "No kill or kill-candidate event exists in the global timeline.",
                notes=["no_kill_events"],
            ))
            return self._objective_result(rule_id, objective, findings)

        for ev in events:
            evidence = ev.get("evidence", {}) or {}
            notif = evidence.get("kill_notification")
            count = evidence.get("count_change")
            delta = evidence.get("time_delta_sec")
            count_conf = self._signal_conf(count)
            notif_conf = self._signal_conf(notif)
            ev_conf = self._event_conf(ev)
            has_count = count_conf >= min_signal
            has_notif = notif_conf >= min_signal
            notes = list(ev.get("notes", []) or [])

            if has_notif and has_count:
                delay_ok = delta is None or self._safe_float(delta, 999.0) <= max_delay
                if delay_ok:
                    conf = self._clamp01(0.50 * count_conf + 0.30 * notif_conf + 0.20 * ev_conf)
                    findings.append(self._make_finding(
                        rule_id,
                        objective,
                        ev,
                        "PASS",
                        conf,
                        "Kill notification and score/count change were linked within the allowed time window.",
                        evidence={"kill_notification_confidence": notif_conf, "count_change_confidence": count_conf, "time_delta_sec": delta},
                        notes=notes,
                    ))
                else:
                    conf = self._clamp01(0.50 * count_conf + 0.30 * notif_conf + 0.20 * ev_conf)
                    findings.append(self._make_finding(
                        rule_id,
                        objective,
                        ev,
                        "FAIL",
                        conf,
                        f"Score/count changed, but the linked change was delayed beyond {max_delay:.2f}s.",
                        evidence={"kill_notification_confidence": notif_conf, "count_change_confidence": count_conf, "time_delta_sec": delta},
                        notes=notes + ["count_change_delay_exceeded"],
                    ))
            elif has_notif and not has_count:
                result = "FAIL" if self.rules.get("require_count_change_after_kill_notification", True) else "UNCERTAIN"
                conf = self._clamp01(0.65 * notif_conf + 0.35 * ev_conf)
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    conf,
                    "Kill notification was detected, but no matching score/count change was found.",
                    evidence={"kill_notification_confidence": notif_conf, "count_change_confidence": count_conf},
                    notes=notes + ["missing_count_change"],
                ))
            elif has_count and not has_notif:
                result = "NEED_REVIEW" if self.rules.get("require_kill_notification_for_count_change", True) else "PASS"
                conf = self._clamp01(0.70 * count_conf + 0.30 * ev_conf)
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    conf,
                    "Score/count change was detected without a matching kill notification, so the kill context is not fully confirmed.",
                    evidence={"count_change_confidence": count_conf, "kill_notification_confidence": notif_conf},
                    notes=notes + ["count_change_without_kill_notification"],
                ))
            else:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "UNCERTAIN",
                    ev_conf,
                    "Kill event exists but both kill notification and count-change evidence are weak or missing.",
                    evidence={"event_confidence": ev_conf, "kill_notification_confidence": notif_conf, "count_change_confidence": count_conf},
                    notes=notes + ["weak_kill_count_evidence"],
                ))

        return self._objective_result(rule_id, objective, findings)

    def evaluate_notification_rule(self, global_timeline: dict[str, Any]) -> ObjectiveResult:
        rule_id = "kill_death_notification"
        objective = "Kill and death notifications should appear when corresponding events occur."
        events = self._global_events(global_timeline)
        kill_events = [e for e in events if e.get("event_type") in {"kill", "kill_candidate"}]
        death_events = [e for e in events if e.get("event_type") in {"death_respawn", "death_missing_respawn", "death_only"}]
        min_signal = float(self.thresholds.get("min_signal_confidence", 0.45))
        findings: list[RuleFinding] = []

        if not kill_events and not death_events:
            findings.append(self._make_finding(
                rule_id,
                objective,
                None,
                "UNCERTAIN",
                0.0,
                "No kill/death event exists in the global timeline, so notification behavior cannot be evaluated.",
                notes=["no_kill_or_death_events"],
            ))
            return self._objective_result(rule_id, objective, findings)

        for ev in kill_events:
            evidence = ev.get("evidence", {}) or {}
            notif = evidence.get("kill_notification")
            count = evidence.get("count_change")
            notif_conf = self._signal_conf(notif)
            count_conf = self._signal_conf(count)
            ev_conf = self._event_conf(ev)
            notes = list(ev.get("notes", []) or [])
            if notif_conf >= min_signal:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "PASS",
                    self._clamp01(0.65 * notif_conf + 0.35 * ev_conf),
                    "Kill notification was detected for the kill event.",
                    evidence={"kill_notification_confidence": notif_conf},
                    notes=notes,
                ))
            elif count_conf >= min_signal:
                if self.rules.get("require_kill_notification_for_count_change", False):
                    result = "FAIL"
                    reason = "Score/count change suggests a kill, but no matching kill notification was detected."
                    extra_notes = ["missing_kill_notification"]
                else:
                    result = "PASS"
                    reason = "Score/count change was detected without kill feed, but score-only candidates are not used as notification failures by current QA config."
                    extra_notes = ["score_only_candidate_ignored"]
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    self._clamp01(0.65 * count_conf + 0.35 * ev_conf),
                    reason,
                    evidence={"count_change_confidence": count_conf, "kill_notification_confidence": notif_conf},
                    notes=notes + extra_notes,
                ))
            else:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "UNCERTAIN",
                    ev_conf,
                    "Kill event exists, but notification evidence is weak and event context is not fully confirmed.",
                    evidence={"event_confidence": ev_conf, "kill_notification_confidence": notif_conf},
                    notes=notes + ["weak_kill_notification_evidence"],
                ))

        for ev in death_events:
            evidence = ev.get("evidence", {}) or {}
            death_notif = evidence.get("death_notification")
            respawn = evidence.get("respawn_segment")
            death_state = evidence.get("death_state")
            notif_conf = self._signal_conf(death_notif)
            resp_conf = self._signal_conf(respawn)
            state_conf = self._signal_conf(death_state)
            ev_conf = self._event_conf(ev)
            notes = list(ev.get("notes", []) or [])
            if notif_conf >= min_signal:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "PASS",
                    self._clamp01(0.65 * notif_conf + 0.35 * ev_conf),
                    "Death/KILLER notification was detected for the death event.",
                    evidence={"death_notification_confidence": notif_conf},
                    notes=notes,
                ))
            else:
                supporting_conf = max(resp_conf, state_conf, ev_conf)
                if supporting_conf >= min_signal and self.rules.get("require_death_notification", True):
                    if self.rules.get("death_notification_optional_if_absent", True):
                        result = "PASS"
                        reason = (
                            "Death/respawn evidence exists, but no matching death/KILLER notification was detected. "
                            "Death panel evidence is configured as optional because that UI can be disabled by the player."
                        )
                        extra_notes = ["missing_optional_death_notification"]
                    else:
                        result = "FAIL"
                        reason = "Death/respawn evidence exists, but no matching death/KILLER notification was detected."
                        extra_notes = ["missing_death_notification"]
                else:
                    result = "UNCERTAIN"
                    reason = "Death event exists, but death notification evidence and supporting signals are weak."
                    extra_notes = ["weak_death_notification_evidence"]
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    supporting_conf,
                    reason,
                    evidence={
                        "death_notification_confidence": notif_conf,
                        "respawn_segment_confidence": resp_conf,
                        "death_state_confidence": state_conf,
                    },
                    notes=notes + extra_notes,
                ))

        return self._objective_result(rule_id, objective, findings)

    def evaluate_respawn_same_space_rule(self, global_timeline: dict[str, Any]) -> ObjectiveResult:
        rule_id = "respawn_same_space"
        objective = "After death, the player should respawn and return to playable HUD state."
        if not self.rules.get("require_respawn_after_death", True):
            return self._objective_result(rule_id, objective, [
                self._make_finding(
                    rule_id,
                    objective,
                    None,
                    "PASS",
                    1.0,
                    "Respawn check is disabled by QA config.",
                    notes=["rule_disabled"],
                )
            ])
        events = [e for e in self._global_events(global_timeline) if e.get("event_type") in {"death_respawn", "death_missing_respawn", "death_only"}]
        spawn_pass = float(self.thresholds.get("spawn_pass_threshold", 0.75))
        spawn_fail = float(self.thresholds.get("spawn_fail_threshold", 0.45))
        findings: list[RuleFinding] = []

        if not events:
            findings.append(self._make_finding(
                rule_id,
                objective,
                None,
                "PASS",
                1.0,
                "No death event exists in the global timeline, so no respawn was required.",
                notes=["no_death_events_no_respawn_required"],
            ))
            return self._objective_result(rule_id, objective, findings)

        for ev in events:
            ev_type = str(ev.get("event_type", ""))
            ev_conf = self._event_conf(ev)
            evidence = ev.get("evidence", {}) or {}
            spawn_signal = evidence.get("spawn_location_check")
            spawn_payload = self._signal_payload(spawn_signal)
            respawn_signal = evidence.get("respawn_segment")
            resp_conf = self._signal_conf(respawn_signal, ev_conf)
            notes = list(ev.get("notes", []) or [])

            if ev_type == "death_missing_respawn":
                result = "FAIL" if self.rules.get("fail_on_missing_respawn", True) else "UNCERTAIN"
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    max(ev_conf, resp_conf),
                    "Death was detected, but no respawn segment was found.",
                    evidence={"event_type": ev_type, "respawn_segment_confidence": resp_conf},
                    notes=notes + ["missing_respawn"],
                ))
                continue

            if ev_type == "death_only":
                result = "FAIL" if self.rules.get("fail_on_death_without_respawn_segment", False) else "UNCERTAIN"
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    ev_conf,
                    "A death event was detected without a linked respawn segment.",
                    evidence={"event_type": ev_type, "event_confidence": ev_conf},
                    notes=notes + ["death_without_respawn_segment"],
                ))
                continue

            if not self.rules.get("require_spawn_location_check", True):
                result = "PASS" if resp_conf >= float(self.thresholds.get("inferred_confidence", 0.50)) else "UNCERTAIN"
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    max(resp_conf, ev_conf),
                    "Respawn segment and playable HUD return were detected. Spawn location text/OCR check is disabled by QA config.",
                    evidence={"event_confidence": ev_conf, "respawn_segment_confidence": resp_conf},
                    notes=notes + ["spawn_location_check_disabled", "respawn_segment_verified"],
                ))
                continue

            if not isinstance(spawn_signal, dict):
                result = "UNCERTAIN" if self.rules.get("require_spawn_location_check", True) else "PASS"
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    result,
                    ev_conf,
                    "Respawn was detected, but no spawn location check was linked.",
                    evidence={"event_confidence": ev_conf, "respawn_segment_confidence": resp_conf},
                    notes=notes + ["missing_spawn_location_check"],
                ))
                continue

            spawn_result = str(spawn_payload.get("result", spawn_signal.get("status", "UNKNOWN"))).upper()
            spawn_score = self._clamp01(spawn_payload.get("final_spawn_score", spawn_payload.get("confidence", self._signal_conf(spawn_signal))))
            expected_spawn = spawn_payload.get("expected_spawn")
            detected_spawn = spawn_payload.get("detected_spawn")
            component_scores = spawn_payload.get("component_scores", {}) if isinstance(spawn_payload.get("component_scores"), dict) else {}

            if spawn_result == "PASS" and spawn_score >= spawn_pass:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "PASS",
                    self._clamp01(0.75 * spawn_score + 0.25 * resp_conf),
                    "Respawn location matched the expected spawn space with sufficient evidence.",
                    evidence={
                        "expected_spawn": expected_spawn,
                        "detected_spawn": detected_spawn,
                        "final_spawn_score": spawn_score,
                        "component_scores": component_scores,
                    },
                    notes=notes,
                ))
            elif spawn_result == "FAIL" or spawn_score <= spawn_fail:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "FAIL",
                    self._clamp01(max(spawn_score, ev_conf)),
                    "Respawn location did not match the expected spawn space, or the spawn score is below the fail threshold.",
                    evidence={
                        "expected_spawn": expected_spawn,
                        "detected_spawn": detected_spawn,
                        "final_spawn_score": spawn_score,
                        "component_scores": component_scores,
                        "spawn_result": spawn_result,
                    },
                    notes=notes + ["spawn_location_mismatch_or_low_score"],
                ))
            elif spawn_result in {"UNCERTAIN", "OBSERVED", "UNKNOWN"}:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "UNCERTAIN",
                    self._clamp01(max(spawn_score, ev_conf * 0.5)),
                    "Respawn was detected, but spawn location evidence is insufficient for PASS/FAIL.",
                    evidence={
                        "expected_spawn": expected_spawn,
                        "detected_spawn": detected_spawn,
                        "final_spawn_score": spawn_score,
                        "component_scores": component_scores,
                        "spawn_result": spawn_result,
                    },
                    notes=notes + ["spawn_location_uncertain"],
                ))
            else:
                findings.append(self._make_finding(
                    rule_id,
                    objective,
                    ev,
                    "NEED_REVIEW",
                    self._clamp01(max(spawn_score, ev_conf)),
                    f"Spawn location result has an unexpected status: {spawn_result}.",
                    evidence={"spawn_result": spawn_result, "final_spawn_score": spawn_score},
                    notes=notes + ["unexpected_spawn_location_status"],
                ))

        return self._objective_result(rule_id, objective, findings)

    def evaluate(self, global_timeline: dict[str, Any]) -> dict[str, Any]:
        objectives = [
            self.evaluate_kill_count_rule(global_timeline),
            self.evaluate_notification_rule(global_timeline),
            self.evaluate_respawn_same_space_rule(global_timeline),
        ]
        summary = self._summary(objectives)
        return {
            "config": self.config,
            "summary": asdict(summary),
            "qa_results": [self._objective_to_dict(o) for o in objectives],
            "source_timeline_summary": global_timeline.get("summary", {}),
        }

    def _objective_to_dict(self, obj: ObjectiveResult) -> dict[str, Any]:
        d = asdict(obj)
        # dataclasses.asdict already expands findings, but keep this explicit for readability.
        d["findings"] = [asdict(f) for f in obj.findings]
        return d

    def _summary(self, objectives: list[ObjectiveResult]) -> QASummary:
        counts = {"PASS": 0, "FAIL": 0, "UNCERTAIN": 0, "NEED_REVIEW": 0}
        for obj in objectives:
            counts[obj.result] = counts.get(obj.result, 0) + 1

        if counts.get("FAIL", 0) > 0:
            overall = "FAIL"
            confs = [o.confidence for o in objectives if o.result == "FAIL"]
            confidence = max(confs) if confs else 0.0
        elif counts.get("NEED_REVIEW", 0) > 0:
            overall = "NEED_REVIEW"
            confs = [o.confidence for o in objectives if o.result == "NEED_REVIEW"]
            confidence = max(confs) if confs else 0.0
        elif counts.get("UNCERTAIN", 0) > 0:
            overall = "UNCERTAIN"
            confs = [o.confidence for o in objectives if o.result == "UNCERTAIN"]
            confidence = float(sum(confs) / len(confs)) if confs else 0.0
        else:
            overall = "PASS"
            confidence = min((o.confidence for o in objectives), default=0.0)

        return QASummary(
            overall_result=overall,
            overall_confidence=self._clamp01(confidence),
            num_objectives=len(objectives),
            num_pass=counts.get("PASS", 0),
            num_fail=counts.get("FAIL", 0),
            num_uncertain=counts.get("UNCERTAIN", 0),
            num_need_review=counts.get("NEED_REVIEW", 0),
            num_findings=sum(len(o.findings) for o in objectives),
        )


def load_qa_rule_config(config_path: Optional[str | Path]) -> dict[str, Any]:
    if not config_path:
        return json.loads(json.dumps(DEFAULT_QA_RULE_CONFIG))
    with Path(config_path).open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_QA_RULE_CONFIG))
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged
