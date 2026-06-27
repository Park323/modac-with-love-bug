from __future__ import annotations

import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


DEFAULT_EVIDENCE_CONFIG: dict[str, Any] = {
    "clip": {
        "pre_sec": 2.0,
        "post_sec": 3.0,
        "fps": 15.0,
        "fourcc": "mp4v",
        "enabled": True,
    },
    "frame": {
        "enabled": True,
        "jpeg_quality": 95,
    },
    "roi": {
        "enabled": True,
        "nearest_ui_max_delta_sec": 0.35,
        "copy_existing_crops": True,
        "crop_from_video_if_missing": True,
    },
    "roi_by_rule": {
        "kill_count_increment": ["top_score_bar", "kill_feed_area"],
        "kill_death_notification": [
            "kill_feed_area",
            "hp_ac_area",
            "weapon_ammo_area",
            "crosshair",
        ],
        "respawn_same_space": [
            "minimap",
            "hp_ac_area",
            "weapon_ammo_area",
            "crosshair",
        ],
        "default": [
            "top_score_bar",
            "kill_feed_area",
            "minimap",
            "hp_ac_area",
            "weapon_ammo_area",
            "crosshair",
        ],
    },
    "status": {
        "include_pass_evidence": True,
        "include_uncertain_evidence": True,
        "include_fail_evidence": True,
        "include_need_review_evidence": True,
    },
}


@dataclass
class EvidenceArtifact:
    kind: str
    path: Optional[str]
    status: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceItem:
    evidence_id: str
    rule_id: str
    objective: str
    result: str
    confidence: float
    time: Optional[float]
    target_event_id: Optional[str]
    target_event_type: Optional[str]
    reason: str
    artifacts: list[EvidenceArtifact]
    global_event: Optional[dict[str, Any]] = None
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    finding: dict[str, Any] = field(default_factory=dict)


class EvidenceReportGenerator:
    """
    Generate human-reviewable evidence from final QA outputs.

    Inputs:
      - qa_rule_report.json from QARuleEngine
      - global_event_timeline.json from GlobalTemporalAggregator
      - optional ui_detection_report.json for ROI crop paths / bbox info
      - optional source video for full-frame snapshots and 3~5 sec clips

    Outputs:
      - report.json
      - report.md
      - evidence/<evidence_id>/full_frame.jpg
      - evidence/<evidence_id>/roi/*.jpg
      - evidence/<evidence_id>/evidence_clip.mp4
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        cfg = json.loads(json.dumps(DEFAULT_EVIDENCE_CONFIG))
        if config:
            self._deep_update(cfg, config)
        self.config = cfg

    @staticmethod
    def _deep_update(base: dict[str, Any], patch: dict[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                EvidenceReportGenerator._deep_update(base[key], value)
            else:
                base[key] = value

    @staticmethod
    def load_json(path: Optional[str | Path]) -> Optional[dict[str, Any]]:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"JSON file not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(value)
        except Exception:
            return default
        if math.isnan(v) or math.isinf(v):
            return default
        return v

    @staticmethod
    def _clamp01(value: Any) -> float:
        v = EvidenceReportGenerator._safe_float(value, 0.0)
        assert v is not None
        return float(max(0.0, min(1.0, v)))

    @staticmethod
    def _ensure_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_name(value: Any, default: str = "item") -> str:
        text = str(value if value is not None else default)
        safe = []
        for ch in text:
            if ch.isalnum() or ch in {"-", "_", "."}:
                safe.append(ch)
            else:
                safe.append("_")
        result = "".join(safe).strip("_")
        return result or default

    @staticmethod
    def _path_relative_to(path: Optional[str | Path], base: Path) -> Optional[str]:
        if path is None:
            return None
        try:
            return str(Path(path).resolve().relative_to(base.resolve()))
        except Exception:
            return str(path)

    def _resolve_path(self, path_value: Optional[str], anchor_file: Optional[str | Path]) -> Optional[Path]:
        if not path_value:
            return None
        raw = Path(path_value)
        candidates = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.append(Path.cwd() / raw)
            if anchor_file:
                anchor = Path(anchor_file)
                candidates.append(anchor.parent / raw)
                # Some reports store paths relative to the directory from which the CLI was run.
                # If crop_path starts with the UI output directory name, anchor.parent/.. may be correct.
                candidates.append(anchor.parent.parent / raw)
        for cand in candidates:
            if cand.exists():
                return cand
        # Return the most likely candidate for error messages even if it does not exist.
        return candidates[0] if candidates else raw

    def _flatten_findings(self, qa_report: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for obj in qa_report.get("qa_results", []) or []:
            if not isinstance(obj, dict):
                continue
            rule_id = str(obj.get("rule_id", "unknown_rule"))
            objective = str(obj.get("objective", rule_id))
            for finding in obj.get("findings", []) or []:
                if isinstance(finding, dict):
                    f = dict(finding)
                    f.setdefault("rule_id", rule_id)
                    f.setdefault("objective", objective)
                    findings.append(f)
        return findings

    def _should_include_finding(self, finding: dict[str, Any]) -> bool:
        result = str(finding.get("result", "UNKNOWN")).upper()
        status_cfg = self.config.get("status", {})
        mapping = {
            "PASS": "include_pass_evidence",
            "UNCERTAIN": "include_uncertain_evidence",
            "FAIL": "include_fail_evidence",
            "NEED_REVIEW": "include_need_review_evidence",
        }
        key = mapping.get(result)
        if key is None:
            return True
        return bool(status_cfg.get(key, True))

    def _index_global_events(self, global_timeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
        events = global_timeline.get("global_events", []) or []
        index: dict[str, dict[str, Any]] = {}
        for ev in events:
            if isinstance(ev, dict) and ev.get("event_id"):
                index[str(ev["event_id"])] = ev
        return index

    def _index_raw_events(self, global_timeline: dict[str, Any]) -> dict[str, dict[str, Any]]:
        events = global_timeline.get("raw_events", []) or []
        index: dict[str, dict[str, Any]] = {}
        for ev in events:
            if isinstance(ev, dict) and ev.get("raw_id"):
                index[str(ev["raw_id"])] = ev
        return index

    def _linked_raw_events(self, global_event: Optional[dict[str, Any]], raw_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(global_event, dict):
            return []
        out = []
        for rid in global_event.get("linked_raw_event_ids", []) or []:
            ev = raw_index.get(str(rid))
            if ev is not None:
                out.append(ev)
        return out

    def _event_time(self, finding: dict[str, Any], global_event: Optional[dict[str, Any]]) -> Optional[float]:
        for source in [finding, global_event or {}]:
            for key in ["time", "respawn_time", "death_time", "start_time"]:
                if key in source and source.get(key) is not None:
                    return self._safe_float(source.get(key), None)
        evidence = finding.get("evidence")
        if isinstance(evidence, dict):
            for key in ["respawn_time", "death_time", "time"]:
                if key in evidence and evidence.get(key) is not None:
                    return self._safe_float(evidence.get(key), None)
        return None

    def _event_clip_range(self, time_sec: Optional[float], global_event: Optional[dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
        if time_sec is None:
            return None, None
        clip_cfg = self.config.get("clip", {})
        pre = float(clip_cfg.get("pre_sec", 2.0))
        post = float(clip_cfg.get("post_sec", 3.0))
        start = max(0.0, time_sec - pre)
        end_anchor = time_sec
        if isinstance(global_event, dict):
            end_time = self._safe_float(global_event.get("end_time"), None)
            if end_time is not None and end_time >= time_sec:
                end_anchor = end_time
        end = max(start + 0.2, end_anchor + post)
        return start, end

    def _nearest_ui_detection(self, ui_report: Optional[dict[str, Any]], time_sec: Optional[float]) -> Optional[dict[str, Any]]:
        if ui_report is None or time_sec is None:
            return None
        detections = ui_report.get("detections", []) or []
        best = None
        best_delta = float("inf")
        for det in detections:
            if not isinstance(det, dict):
                continue
            ts = self._safe_float(det.get("timestamp_sec"), None)
            if ts is None:
                continue
            delta = abs(ts - time_sec)
            if delta < best_delta:
                best = det
                best_delta = delta
        max_delta = float(self.config.get("roi", {}).get("nearest_ui_max_delta_sec", 0.35))
        if best is not None and best_delta <= max_delta:
            return best
        return None

    def _roi_names_for_finding(self, finding: dict[str, Any], global_event: Optional[dict[str, Any]]) -> list[str]:
        rule_id = str(finding.get("rule_id", "default"))
        roi_by_rule = self.config.get("roi_by_rule", {})
        names = roi_by_rule.get(rule_id)
        if names is None:
            event_type = str((global_event or {}).get("event_type", ""))
            if event_type == "kill":
                names = roi_by_rule.get("kill_count_increment")
            elif event_type in {"death_respawn", "death_only"}:
                names = roi_by_rule.get("respawn_same_space")
            else:
                names = roi_by_rule.get("default", [])
        return [str(n) for n in names or []]

    def _open_video(self, video_path: Optional[str | Path]) -> Optional[cv2.VideoCapture]:
        if not video_path:
            return None
        p = Path(video_path)
        if not p.exists():
            return None
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _read_video_frame(self, cap: Optional[cv2.VideoCapture], time_sec: Optional[float]) -> Optional[np.ndarray]:
        if cap is None or time_sec is None:
            return None
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(time_sec * fps))))
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_sec * 1000.0))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        return frame

    def _write_full_frame(
        self,
        cap: Optional[cv2.VideoCapture],
        time_sec: Optional[float],
        evidence_dir: Path,
    ) -> EvidenceArtifact:
        if not bool(self.config.get("frame", {}).get("enabled", True)):
            return EvidenceArtifact("full_frame", None, "disabled")
        frame = self._read_video_frame(cap, time_sec)
        if frame is None:
            return EvidenceArtifact("full_frame", None, "missing", "source video or event time unavailable")
        path = evidence_dir / "full_frame.jpg"
        quality = int(self.config.get("frame", {}).get("jpeg_quality", 95))
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return EvidenceArtifact("full_frame", str(path), "ok", metadata={"time_sec": time_sec})

    def _write_clip(
        self,
        video_path: Optional[str | Path],
        time_sec: Optional[float],
        global_event: Optional[dict[str, Any]],
        evidence_dir: Path,
    ) -> EvidenceArtifact:
        clip_cfg = self.config.get("clip", {})
        if not bool(clip_cfg.get("enabled", True)):
            return EvidenceArtifact("clip", None, "disabled")
        if not video_path or time_sec is None or not Path(video_path).exists():
            return EvidenceArtifact("clip", None, "missing", "source video or event time unavailable")

        start, end = self._event_clip_range(time_sec, global_event)
        if start is None or end is None:
            return EvidenceArtifact("clip", None, "missing", "invalid clip range")

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return EvidenceArtifact("clip", None, "missing", "failed to open source video")

        src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            cap.release()
            return EvidenceArtifact("clip", None, "missing", "invalid source video resolution")

        out_fps = float(clip_cfg.get("fps", 15.0))
        if out_fps <= 0:
            out_fps = min(src_fps, 15.0) if src_fps > 0 else 15.0
        sample_interval = max(1, int(round(src_fps / out_fps))) if src_fps > 0 else 1

        fourcc_str = str(clip_cfg.get("fourcc", "mp4v"))[:4].ljust(4)
        fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
        out_path = evidence_dir / "evidence_clip.mp4"
        writer = cv2.VideoWriter(str(out_path), fourcc, out_fps, (width, height))
        if not writer.isOpened():
            cap.release()
            return EvidenceArtifact("clip", None, "missing", "failed to open video writer")

        start_frame = max(0, int(math.floor(start * src_fps))) if src_fps > 0 else 0
        end_frame = max(start_frame + 1, int(math.ceil(end * src_fps))) if src_fps > 0 else start_frame + 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_idx = start_frame
        written = 0
        while frame_idx <= end_frame:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if (frame_idx - start_frame) % sample_interval == 0:
                writer.write(frame)
                written += 1
            frame_idx += 1
        writer.release()
        cap.release()
        if written <= 0:
            if out_path.exists():
                out_path.unlink()
            return EvidenceArtifact("clip", None, "missing", "no frames written")
        return EvidenceArtifact(
            "clip",
            str(out_path),
            "ok",
            metadata={"start_sec": start, "end_sec": end, "fps": out_fps, "num_frames": written},
        )

    def _copy_or_crop_roi(
        self,
        roi_name: str,
        ui_detection: Optional[dict[str, Any]],
        ui_report_path: Optional[str | Path],
        cap: Optional[cv2.VideoCapture],
        time_sec: Optional[float],
        roi_dir: Path,
    ) -> EvidenceArtifact:
        if not bool(self.config.get("roi", {}).get("enabled", True)):
            return EvidenceArtifact(f"roi:{roi_name}", None, "disabled")
        if ui_detection is None:
            return EvidenceArtifact(f"roi:{roi_name}", None, "missing", "no nearby UI detection")
        regions = ui_detection.get("regions", {}) or {}
        region = regions.get(roi_name)
        if not isinstance(region, dict):
            return EvidenceArtifact(f"roi:{roi_name}", None, "missing", "ROI not found in UI detection")

        self._ensure_dir(roi_dir)
        dst = roi_dir / f"{roi_name}.jpg"
        crop_path = region.get("crop_path")
        copy_existing = bool(self.config.get("roi", {}).get("copy_existing_crops", True))
        if copy_existing and crop_path:
            src = self._resolve_path(str(crop_path), ui_report_path)
            if src is not None and src.exists():
                shutil.copy2(src, dst)
                return EvidenceArtifact(
                    f"roi:{roi_name}",
                    str(dst),
                    "ok",
                    "copied existing UI crop",
                    metadata={"source_crop_path": str(src), "ui_timestamp_sec": ui_detection.get("timestamp_sec")},
                )

        crop_from_video = bool(self.config.get("roi", {}).get("crop_from_video_if_missing", True))
        if not crop_from_video:
            return EvidenceArtifact(f"roi:{roi_name}", None, "missing", "existing crop unavailable")
        frame = self._read_video_frame(cap, time_sec)
        if frame is None:
            return EvidenceArtifact(f"roi:{roi_name}", None, "missing", "cannot crop because source video frame unavailable")
        bbox = region.get("bbox_original") or region.get("bbox_corrected") or region.get("bbox_base")
        if not isinstance(bbox, list) or len(bbox) != 4:
            return EvidenceArtifact(f"roi:{roi_name}", None, "missing", "invalid ROI bbox")
        x, y, w, h = [int(round(float(v))) for v in bbox]
        H, W = frame.shape[:2]
        x0 = max(0, min(W, x))
        y0 = max(0, min(H, y))
        x1 = max(0, min(W, x + max(0, w)))
        y1 = max(0, min(H, y + max(0, h)))
        if x1 <= x0 or y1 <= y0:
            return EvidenceArtifact(f"roi:{roi_name}", None, "missing", "ROI bbox outside frame")
        crop = frame[y0:y1, x0:x1]
        cv2.imwrite(str(dst), crop, [cv2.IMWRITE_JPEG_QUALITY, int(self.config.get("frame", {}).get("jpeg_quality", 95))])
        return EvidenceArtifact(
            f"roi:{roi_name}",
            str(dst),
            "ok",
            "cropped from source video using UI bbox",
            metadata={"bbox_original": [x0, y0, x1 - x0, y1 - y0], "ui_timestamp_sec": ui_detection.get("timestamp_sec")},
        )

    def _write_event_manifest(self, item: EvidenceItem, evidence_dir: Path) -> EvidenceArtifact:
        path = evidence_dir / "evidence_item.json"
        payload = asdict(item)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return EvidenceArtifact("event_manifest", str(path), "ok")

    def generate(
        self,
        qa_report: dict[str, Any],
        global_timeline: dict[str, Any],
        output_dir: str | Path,
        *,
        video_path: Optional[str | Path] = None,
        ui_report: Optional[dict[str, Any]] = None,
        ui_report_path: Optional[str | Path] = None,
        input_reports: Optional[dict[str, Optional[str]]] = None,
        write_markdown: bool = True,
    ) -> dict[str, Any]:
        out_dir = Path(output_dir)
        evidence_root = out_dir / "evidence"
        self._ensure_dir(evidence_root)

        global_index = self._index_global_events(global_timeline)
        raw_index = self._index_raw_events(global_timeline)
        findings = self._flatten_findings(qa_report)

        cap = self._open_video(video_path)
        evidence_items: list[EvidenceItem] = []

        for idx, finding in enumerate(findings, start=1):
            if not self._should_include_finding(finding):
                continue
            target_id = finding.get("target_event_id")
            global_event = global_index.get(str(target_id)) if target_id else None
            raw_events = self._linked_raw_events(global_event, raw_index)
            time_sec = self._event_time(finding, global_event)

            rule_id = str(finding.get("rule_id", "unknown_rule"))
            result = str(finding.get("result", "UNKNOWN")).upper()
            eid_base = target_id or f"finding_{idx:04d}"
            evidence_id = f"ev_{idx:04d}_{self._sanitize_name(rule_id)}_{self._sanitize_name(eid_base)}"
            evidence_dir = evidence_root / evidence_id
            self._ensure_dir(evidence_dir)

            artifacts: list[EvidenceArtifact] = []
            artifacts.append(self._write_full_frame(cap, time_sec, evidence_dir))
            artifacts.append(self._write_clip(video_path, time_sec, global_event, evidence_dir))

            ui_det = self._nearest_ui_detection(ui_report, time_sec)
            roi_dir = evidence_dir / "roi"
            for roi_name in self._roi_names_for_finding(finding, global_event):
                artifacts.append(self._copy_or_crop_roi(roi_name, ui_det, ui_report_path, cap, time_sec, roi_dir))

            item = EvidenceItem(
                evidence_id=evidence_id,
                rule_id=rule_id,
                objective=str(finding.get("objective", rule_id)),
                result=result,
                confidence=self._clamp01(finding.get("confidence", 0.0)),
                time=time_sec,
                target_event_id=str(target_id) if target_id is not None else None,
                target_event_type=finding.get("target_event_type"),
                reason=str(finding.get("reason", "")),
                artifacts=artifacts,
                global_event=global_event,
                raw_events=raw_events,
                finding=finding,
            )
            # Add a manifest artifact after the item exists.
            manifest_artifact = self._write_event_manifest(item, evidence_dir)
            item.artifacts.append(manifest_artifact)
            evidence_items.append(item)

        if cap is not None:
            cap.release()

        summary = self._summarize(qa_report, evidence_items)
        report = {
            "summary": summary,
            "source": {
                "video_path": str(video_path) if video_path else None,
                "qa_rule_report": input_reports.get("qa_rule_report") if input_reports else None,
                "global_timeline": input_reports.get("global_timeline") if input_reports else None,
                "ui_report": input_reports.get("ui_report") if input_reports else None,
                "all_input_reports": input_reports or {},
            },
            "qa_summary": qa_report.get("summary", {}),
            "qa_results": qa_report.get("qa_results", []),
            "global_timeline_summary": global_timeline.get("summary", {}),
            "evidence_items": [asdict(item) for item in evidence_items],
        }

        report_path = out_dir / "report.json"
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        md_path = None
        if write_markdown:
            md_path = out_dir / "report.md"
            self.write_markdown(report, md_path, base_dir=out_dir)

        report["output_files"] = {
            "report_json": str(report_path),
            "report_md": str(md_path) if md_path else None,
            "evidence_dir": str(evidence_root),
        }
        # Rewrite JSON with output_files included.
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report

    def _summarize(self, qa_report: dict[str, Any], evidence_items: list[EvidenceItem]) -> dict[str, Any]:
        qa_summary = qa_report.get("summary", {}) if isinstance(qa_report, dict) else {}
        artifact_counts: dict[str, int] = {"ok": 0, "missing": 0, "disabled": 0}
        for item in evidence_items:
            for artifact in item.artifacts:
                artifact_counts[artifact.status] = artifact_counts.get(artifact.status, 0) + 1
        by_result: dict[str, int] = {}
        for item in evidence_items:
            by_result[item.result] = by_result.get(item.result, 0) + 1
        return {
            "overall_result": qa_summary.get("overall_result"),
            "overall_confidence": qa_summary.get("overall_confidence"),
            "num_evidence_items": len(evidence_items),
            "num_artifacts_ok": artifact_counts.get("ok", 0),
            "num_artifacts_missing": artifact_counts.get("missing", 0),
            "num_artifacts_disabled": artifact_counts.get("disabled", 0),
            "evidence_items_by_result": by_result,
        }

    def write_markdown(self, report: dict[str, Any], path: str | Path, *, base_dir: Optional[Path] = None) -> None:
        path = Path(path)
        base_dir = base_dir or path.parent
        summary = report.get("summary", {})
        qa_summary = report.get("qa_summary", {})
        lines: list[str] = []
        lines.append("# CrossFire QA Evidence Report")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **overall_result**: {summary.get('overall_result')}")
        try:
            conf_str = f"{float(summary.get('overall_confidence', 0.0)):.3f}"
        except Exception:
            conf_str = str(summary.get("overall_confidence"))
        lines.append(f"- **overall_confidence**: {conf_str}")
        lines.append(f"- **num_evidence_items**: {summary.get('num_evidence_items')}")
        lines.append(f"- **artifacts ok/missing/disabled**: {summary.get('num_artifacts_ok')} / {summary.get('num_artifacts_missing')} / {summary.get('num_artifacts_disabled')}")
        lines.append("")

        lines.append("## QA Objectives")
        lines.append("")
        lines.append("| objective | result | confidence | summary |")
        lines.append("|---|---|---:|---|")
        for obj in report.get("qa_results", []) or []:
            try:
                conf = float(obj.get("confidence", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            lines.append(f"| {obj.get('rule_id')} | {obj.get('result')} | {conf:.3f} | {obj.get('summary', '')} |")
        if not report.get("qa_results"):
            lines.append(f"| overall | {qa_summary.get('overall_result')} | {float(qa_summary.get('overall_confidence', 0.0) or 0.0):.3f} | final QA summary |")
        lines.append("")

        lines.append("## Evidence Items")
        lines.append("")
        for item in report.get("evidence_items", []) or []:
            evidence_id = item.get("evidence_id")
            lines.append(f"### {evidence_id} — {item.get('result')} — {item.get('rule_id')}")
            lines.append("")
            time = item.get("time")
            time_str = "N/A" if time is None else f"{float(time):.3f}s"
            lines.append(f"- **time**: {time_str}")
            lines.append(f"- **target_event**: {item.get('target_event_id')} / {item.get('target_event_type')}")
            lines.append(f"- **confidence**: {float(item.get('confidence', 0.0) or 0.0):.3f}")
            lines.append(f"- **reason**: {item.get('reason')}")
            lines.append("")
            lines.append("Artifacts:")
            for artifact in item.get("artifacts", []) or []:
                art_path = artifact.get("path")
                kind = artifact.get("kind")
                status = artifact.get("status")
                if art_path:
                    rel = self._path_relative_to(art_path, base_dir)
                    lines.append(f"- `{kind}` **{status}**: [{Path(str(art_path)).name}]({rel})")
                else:
                    msg = artifact.get("message", "")
                    lines.append(f"- `{kind}` **{status}**: {msg}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


def load_evidence_config(path: Optional[str | Path]) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)
