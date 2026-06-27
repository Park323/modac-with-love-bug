from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
DETECTOR_DIR = ROOT_DIR / "detector_layer"
REPORT_DIR = ROOT_DIR / "report_layer"
CONFIG_DIR = ROOT_DIR / "configs"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def _default_config(name: str) -> str | None:
    path = CONFIG_DIR / name
    return str(path) if path.exists() else None


def _safe_name(path: Path) -> str:
    stem = path.stem.strip() or "video"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)


def _json_load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _json_dump(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_optional(cmd: list[str], flag: str, value: str | None) -> None:
    if value:
        cmd.extend([flag, value])


def _resolve_optional_path(value: str | None) -> str | None:
    if not value:
        return None
    return str(Path(value).expanduser().resolve())


def _qa_requires_spawn_location(qa_config: str | None) -> bool:
    if not qa_config:
        return False
    path = Path(qa_config).expanduser()
    if not path.exists():
        return False
    try:
        cfg = _json_load(path) or {}
    except Exception:
        return False
    rules = cfg.get("rules", {}) if isinstance(cfg.get("rules"), dict) else {}
    return bool(rules.get("require_spawn_location_check", False))


def discover_videos(dataset: Path) -> list[Path]:
    if dataset.is_file():
        if dataset.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Unsupported video extension: {dataset}")
        return [dataset.resolve()]
    if not dataset.exists():
        raise FileNotFoundError(f"Dataset path not found: {dataset}")
    videos = [p.resolve() for p in dataset.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    return sorted(videos)


def run_command(cmd: list[str], cwd: Path, dry_run: bool = False) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    if dry_run:
        return {
            "command": cmd,
            "cwd": str(cwd),
            "returncode": 0,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stdout": "",
            "stderr": "",
            "dry_run": True,
        }

    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    return {
        "command": cmd,
        "cwd": str(cwd),
        "returncode": proc.returncode,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "dry_run": False,
    }


def run_stage(
    *,
    name: str,
    cmd: list[str],
    cwd: Path,
    video_summary: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    print(f"[{video_summary['video_name']}] {name} ...", flush=True)
    result = run_command(cmd, cwd=cwd, dry_run=args.dry_run)
    video_summary["stages"].append({"name": name, **result})
    if result["returncode"] != 0:
        video_summary["status"] = "FAILED"
        video_summary["failed_stage"] = name
        print(f"[{video_summary['video_name']}] {name} failed", file=sys.stderr, flush=True)
        if not args.keep_going:
            raise RuntimeError(f"Stage failed: {name}")
        return False
    return True


def build_stage_commands(video: Path, video_out: Path, args: argparse.Namespace) -> list[tuple[str, list[str], Path]]:
    ui_dir = video_out / "01_ui"
    kill_dir = video_out / "02_kill_count"
    notifications_dir = video_out / "03_notifications"
    game_state_dir = video_out / "04_game_state"
    respawn_dir = video_out / "05_respawn"
    spawn_dir = video_out / "06_spawn_location"
    global_dir = video_out / "07_global_timeline"
    qa_dir = video_out / "08_qa_rules"
    evidence_dir = video_out / "09_evidence_report"

    ui_report = ui_dir / "ui_detection_report.json"
    kill_report = kill_dir / "kill_count_report.json"
    notification_report = notifications_dir / "notification_report.json"
    game_state_report = game_state_dir / "game_state_report.json"
    respawn_report = respawn_dir / "respawn_segment_report.json"
    spawn_report = spawn_dir / "spawn_location_report.json"
    global_report = global_dir / "global_event_timeline.json"
    qa_report = qa_dir / "qa_rule_report.json"

    ui_cmd = [
        sys.executable,
        "run_ui_detection.py",
        "--video",
        str(video),
        "--out",
        str(ui_dir),
        "--sample-fps",
        str(args.sample_fps),
        "--resize",
        args.resize,
        "--base-resolution",
        args.base_resolution,
        "--save-crops",
    ]
    _append_optional(ui_cmd, "--roi-config", _resolve_optional_path(args.roi_config))
    _append_optional(ui_cmd, "--templates", _resolve_optional_path(args.ui_templates))
    if args.save_overlays:
        ui_cmd.append("--save-overlays")
    if args.max_frames is not None:
        ui_cmd.extend(["--max-frames", str(args.max_frames)])
    if args.no_normalize:
        ui_cmd.append("--no-normalize")
    if args.no_anchor_correction:
        ui_cmd.append("--no-anchor-correction")
    if args.no_template_verify:
        ui_cmd.append("--no-template-verify")

    kill_cmd = [
        sys.executable,
        "run_kill_count_reader.py",
        "--ui-report",
        str(ui_report),
        "--out",
        str(kill_dir),
        "--backend",
        args.score_backend,
        "--min-digit-confidence",
        str(args.min_digit_confidence),
        "--score-sample-interval-sec",
        str(args.score_sample_interval_sec),
        "--kill-feed-events",
        str(notification_report),
    ]
    _append_optional(kill_cmd, "--score-config", _resolve_optional_path(args.score_config))
    _append_optional(kill_cmd, "--digit-templates", _resolve_optional_path(args.digit_templates))
    _append_optional(kill_cmd, "--easyocr-model-dir", _resolve_optional_path(args.easyocr_model_dir))
    if args.score_backend == "vlm":
        kill_cmd.extend(["--vlm-model", args.vlm_model, "--vlm-base-url", args.vlm_base_url])
        kill_cmd.extend(["--vlm-sample-interval-sec", str(args.vlm_sample_interval_sec)])
        _append_optional(kill_cmd, "--vlm-api-key-file", _resolve_optional_path(args.vlm_api_key_file))
    if args.max_frames is not None:
        kill_cmd.extend(["--max-frames", str(args.max_frames)])

    notification_cmd = [
        sys.executable,
        "run_notification_detector.py",
        "--ui-report",
        str(ui_report),
        "--out",
        str(notifications_dir),
    ]
    _append_optional(notification_cmd, "--notification-config", _resolve_optional_path(args.notification_config))
    _append_optional(notification_cmd, "--notification-templates", _resolve_optional_path(args.notification_templates))
    if args.max_frames is not None:
        notification_cmd.extend(["--max-frames", str(args.max_frames)])
    if args.no_heuristics:
        notification_cmd.append("--no-heuristics")

    game_state_cmd = [
        sys.executable,
        "run_game_state_classifier.py",
        "--ui-report",
        str(ui_report),
        "--out",
        str(game_state_dir),
        "--notification-report",
        str(notification_report),
    ]
    _append_optional(game_state_cmd, "--state-config", _resolve_optional_path(args.game_state_config))
    _append_optional(game_state_cmd, "--state-templates", _resolve_optional_path(args.state_templates))
    if args.max_frames is not None:
        game_state_cmd.extend(["--max-frames", str(args.max_frames)])
    if args.no_heuristics:
        game_state_cmd.append("--no-heuristics")

    respawn_cmd = [
        sys.executable,
        "run_respawn_segment_detector.py",
        "--game-state-report",
        str(game_state_report),
        "--notification-report",
        str(notification_report),
        "--out",
        str(respawn_dir),
    ]
    _append_optional(respawn_cmd, "--respawn-config", _resolve_optional_path(args.respawn_config))

    spawn_cmd = [
        sys.executable,
        "run_spawn_location_recognizer.py",
        "--video",
        str(video),
        "--respawn-report",
        str(respawn_report),
        "--out",
        str(spawn_dir),
        "--sample-fps",
        str(args.spawn_sample_fps),
        "--resize",
        args.resize,
        "--base-resolution",
        args.base_resolution,
    ]
    _append_optional(spawn_cmd, "--spawn-config", _resolve_optional_path(args.spawn_config))
    _append_optional(spawn_cmd, "--expected-spawn", args.expected_spawn)
    _append_optional(spawn_cmd, "--spawn-references", _resolve_optional_path(args.spawn_references))
    _append_optional(spawn_cmd, "--roi-config", _resolve_optional_path(args.roi_config))
    _append_optional(spawn_cmd, "--ui-templates", _resolve_optional_path(args.ui_templates))
    _append_optional(spawn_cmd, "--easyocr-model-dir", _resolve_optional_path(args.easyocr_model_dir))
    if args.max_spawn_checks and args.max_spawn_checks > 0:
        spawn_cmd.extend(["--max-spawn-checks", str(args.max_spawn_checks)])
    if args.disable_easyocr:
        spawn_cmd.append("--disable-easyocr")
    if args.install_easyocr:
        spawn_cmd.append("--install-easyocr")
    if args.save_debug_crops:
        spawn_cmd.append("--save-debug-crops")
    if args.no_normalize:
        spawn_cmd.append("--no-normalize")
    if args.no_anchor_correction:
        spawn_cmd.append("--no-anchor-correction")

    empty_spawn_cmd = [
        sys.executable,
        "write_empty_spawn_location_report.py",
        "--respawn-report",
        str(respawn_report),
        "--out",
        str(spawn_dir),
        "--reason",
        "skip_spawn_location_option",
    ]

    global_cmd = [
        sys.executable,
        "run_global_temporal_aggregator.py",
        "--kill-count-report",
        str(kill_report),
        "--notification-report",
        str(notification_report),
        "--game-state-report",
        str(game_state_report),
        "--respawn-report",
        str(respawn_report),
        "--spawn-location-report",
        str(spawn_report),
        "--out",
        str(global_dir),
        "--pretty",
    ]
    _append_optional(global_cmd, "--global-config", _resolve_optional_path(args.global_config))

    qa_cmd = [
        sys.executable,
        "run_qa_rule_engine.py",
        "--global-timeline",
        str(global_report),
        "--out",
        str(qa_dir),
        "--pretty",
    ]
    _append_optional(qa_cmd, "--qa-config", _resolve_optional_path(args.qa_config))

    evidence_cmd = [
        sys.executable,
        "run_evidence_report_generator.py",
        "--qa-rule-report",
        str(qa_report),
        "--global-timeline",
        str(global_report),
        "--ui-report",
        str(ui_report),
        "--video",
        str(video),
        "--out",
        str(evidence_dir),
    ]
    _append_optional(evidence_cmd, "--evidence-config", _resolve_optional_path(args.evidence_config))

    skip_spawn_location = args.skip_spawn_location or not _qa_requires_spawn_location(args.qa_config)

    return [
        ("ui_detection", ui_cmd, DETECTOR_DIR),
        ("notification_detector", notification_cmd, DETECTOR_DIR),
        ("kill_count_reader", kill_cmd, DETECTOR_DIR),
        ("game_state_classifier", game_state_cmd, DETECTOR_DIR),
        ("respawn_segment_detector", respawn_cmd, DETECTOR_DIR),
        ("spawn_location_recognizer", empty_spawn_cmd if skip_spawn_location else spawn_cmd, DETECTOR_DIR),
        ("global_temporal_aggregator", global_cmd, REPORT_DIR),
        ("qa_rule_engine", qa_cmd, REPORT_DIR),
        ("evidence_report_generator", evidence_cmd, REPORT_DIR),
    ]


def summarize_video(video: Path, video_out: Path, stages_ok: bool) -> dict[str, Any]:
    qa_report = _json_load(video_out / "08_qa_rules" / "qa_rule_report.json") or {}
    evidence_report = _json_load(video_out / "09_evidence_report" / "report.json") or {}
    global_report = _json_load(video_out / "07_global_timeline" / "global_event_timeline.json") or {}
    spawn_report = _json_load(video_out / "06_spawn_location" / "spawn_location_report.json") or {}
    respawn_report = _json_load(video_out / "05_respawn" / "respawn_segment_report.json") or {}

    return {
        "video": str(video),
        "video_name": video.name,
        "status": "COMPLETED" if stages_ok else "FAILED",
        "qa_summary": qa_report.get("summary", {}),
        "evidence_summary": evidence_report.get("summary", {}),
        "global_summary": global_report.get("summary", {}),
        "respawn_summary": respawn_report.get("respawn_detection", {}).get("summary", {}),
        "spawn_summary": spawn_report.get("summary", {}),
        "reports": {
            "ui": str(video_out / "01_ui" / "ui_detection_report.json"),
            "kill_count": str(video_out / "02_kill_count" / "kill_count_report.json"),
            "notifications": str(video_out / "03_notifications" / "notification_report.json"),
            "game_state": str(video_out / "04_game_state" / "game_state_report.json"),
            "respawn": str(video_out / "05_respawn" / "respawn_segment_report.json"),
            "spawn_location": str(video_out / "06_spawn_location" / "spawn_location_report.json"),
            "global_timeline": str(video_out / "07_global_timeline" / "global_event_timeline.json"),
            "qa_rules": str(video_out / "08_qa_rules" / "qa_rule_report.json"),
            "evidence": str(video_out / "09_evidence_report" / "report.json"),
            "evidence_markdown": str(video_out / "09_evidence_report" / "report.md"),
        },
    }


def build_dataset_summary(video_results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [r for r in video_results if r.get("status") == "COMPLETED"]
    failed = [r for r in video_results if r.get("status") != "COMPLETED"]
    overall_counts: dict[str, int] = {}
    for result in completed:
        qa = result.get("qa_summary", {})
        key = str(qa.get("overall_result", "UNKNOWN"))
        overall_counts[key] = overall_counts.get(key, 0) + 1
    return {
        "num_videos": len(video_results),
        "num_completed": len(completed),
        "num_failed": len(failed),
        "overall_result_counts": overall_counts,
    }


def _relpath(path_value: str | None, base_dir: Path) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        return str(path.resolve().relative_to(base_dir.resolve()))
    except Exception:
        return str(path)


def _roi_layout(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _json_load(Path(args.roi_config)) if args.roi_config else None
    if not cfg:
        return {"resolution": args.base_resolution}
    layout: dict[str, Any] = {"resolution": "x".join(str(x) for x in cfg.get("base_resolution", [])) or args.base_resolution}
    for name, roi in (cfg.get("rois") or {}).items():
        layout[name] = [roi.get("x", 0), roi.get("y", 0), roi.get("w", 0), roi.get("h", 0)]
    return layout


def _rule_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _json_load(Path(args.qa_config)) if args.qa_config else None
    if not cfg:
        return {}
    return {
        "thresholds": cfg.get("thresholds", {}),
        "rules": cfg.get("rules", {}),
    }


def _check_type(rule_id: str) -> str:
    mapping = {
        "kill_count_increment": "kill_count_after_kill_notification",
        "kill_death_notification": "kill_death_notification_presence",
        "respawn_same_space": "respawn_operation_after_death",
    }
    return mapping.get(rule_id, rule_id)


def _artifact_type(kind: str) -> str:
    if kind.startswith("roi:"):
        return "crop"
    if kind == "clip":
        return "clip"
    if kind == "full_frame":
        return "full_frame"
    return kind


def _overall_from_counts(counts: dict[str, int]) -> str:
    for result in ["FAIL", "NEED_REVIEW", "UNCERTAIN", "PASS"]:
        if counts.get(result, 0) > 0:
            return result
    return "UNKNOWN"


def _git_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT_DIR),
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _config_hash(args: argparse.Namespace) -> str:
    paths = [
        args.roi_config,
        args.score_config,
        args.notification_config,
        args.game_state_config,
        args.respawn_config,
        args.spawn_config,
        args.global_config,
        args.qa_config,
        args.evidence_config,
    ]
    h = hashlib.sha256()
    for value in paths:
        if not value:
            continue
        path = Path(value)
        h.update(str(path).encode("utf-8"))
        if path.exists():
            h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _template_library_version(args: argparse.Namespace) -> str:
    roots = [args.notification_templates, args.ui_templates, args.state_templates, args.digit_templates, args.spawn_references]
    mtimes: list[float] = []
    for root in roots:
        if not root:
            continue
        path = Path(root)
        if path.is_file():
            mtimes.append(path.stat().st_mtime)
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    mtimes.append(child.stat().st_mtime)
    if not mtimes:
        return "none"
    return datetime.fromtimestamp(max(mtimes), timezone.utc).date().isoformat()


def _run_reproducibility(args: argparse.Namespace, out_root: Path) -> dict[str, Any]:
    return {
        "pipeline_version": out_root.name,
        "git_commit": _git_commit(),
        "config_hash": _config_hash(args),
        "template_library_version": _template_library_version(args),
        "ocr_backend": "easyocr" if not getattr(args, "disable_easyocr", False) else "disabled",
        "score_backend": args.score_backend,
        "model_versions": {
            "notification_detector": "template_v3+death_panel_structure_v1",
            "game_state_classifier": "heuristic_v2",
            "spawn_location_recognizer": "ocr_ensemble_v1",
            "score_reader": f"{args.score_backend}_v1",
        },
        "command": " ".join(sys.argv),
    }


def _time_range(item: dict[str, Any]) -> list[float] | None:
    time_value = item.get("time")
    if time_value is None:
        return None
    end_time = item.get("end_time")
    if end_time is None:
        return [float(time_value), float(time_value)]
    return [float(time_value), float(end_time)]


def _event_from_global_event(global_event: dict[str, Any], video_id: str) -> dict[str, Any]:
    event_id = f"{video_id}_{global_event.get('event_id', 'event')}"
    event: dict[str, Any] = {
        "event_id": event_id,
        "video_id": video_id,
        "event_type": global_event.get("event_type"),
        "status": global_event.get("status", "UNKNOWN"),
        "time_sec": global_event.get("time"),
        "confidence": global_event.get("confidence", 0.0),
        "source_signals": {
            "source_modules": global_event.get("source", []),
            "linked_raw_event_ids": global_event.get("linked_raw_event_ids", []),
        },
    }
    time_range = _time_range(global_event)
    if time_range:
        event["time_range_sec"] = time_range
    if global_event.get("evidence"):
        event["evidence"] = global_event.get("evidence")
    return event


def _collect_evidence_artifacts(
    evidence_report: dict[str, Any],
    rule_id: str,
    target_event_id: str | None,
    out_root: Path,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in evidence_report.get("evidence_items", []) or []:
        if item.get("rule_id") != rule_id:
            continue
        if target_event_id and item.get("target_event_id") not in {None, target_event_id}:
            continue
        for artifact in item.get("artifacts", []) or []:
            if artifact.get("status") != "ok" or not artifact.get("path"):
                continue
            artifacts.append({
                "path": _relpath(artifact.get("path"), out_root),
                "type": _artifact_type(str(artifact.get("kind", ""))),
                "kind": artifact.get("kind"),
            })
    return artifacts


def _collect_evidence_item_paths(
    evidence_report: dict[str, Any],
    rule_id: str,
    target_event_id: str | None,
    out_root: Path,
) -> list[str]:
    paths: list[str] = []
    for item in evidence_report.get("evidence_items", []) or []:
        if item.get("rule_id") != rule_id:
            continue
        if target_event_id and item.get("target_event_id") not in {None, target_event_id}:
            continue
        item_path = item.get("item_path") or item.get("path")
        if item_path:
            paths.append(_relpath(item_path, out_root) or str(item_path))
    return paths


def _event_lookup(global_report: dict[str, Any], video_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in global_report.get("global_events", []) or []:
        event_id = str(event.get("event_id", ""))
        if event_id:
            out[f"{video_id}_{event_id}"] = event
            out[event_id] = event
    return out


def _parse_spawn_conflict(notes: list[str]) -> dict[str, Any] | None:
    for note in notes:
        m = re.search(r"location_text_conflict:([^!=@]+)!=([^@]+)@([0-9.]+)", note)
        if not m:
            continue
        return {
            "observed": m.group(1).strip(),
            "expected": m.group(2).strip(),
            "confidence": float(m.group(3)),
        }
    return None


def _condition_result(observed: Any, expected: Any, confidence: float, threshold: float) -> str:
    if observed == expected and confidence >= threshold:
        return "satisfied"
    if confidence >= threshold:
        return "conflict"
    return "not_satisfied"


def _decision_trace(
    *,
    rule_id: str,
    finding: dict[str, Any],
    related_events: list[str],
    rule_cfg: dict[str, Any],
) -> dict[str, Any]:
    thresholds = rule_cfg.get("thresholds", {}) or {}
    rules = rule_cfg.get("rules", {}) or {}
    min_signal = float(thresholds.get("min_signal_confidence", 0.45))
    notes = [str(n) for n in finding.get("notes", []) or []]
    observed = finding.get("evidence", {}) or {}
    conditions: list[dict[str, Any]] = []

    if rule_id == "kill_death_notification":
        target_type = str(finding.get("target_event_type", ""))
        if target_type == "kill":
            conf = float(observed.get("kill_notification_confidence", 0.0) or 0.0)
            conditions.append({
                "condition": "kill_notification_detected",
                "expected": True,
                "observed": conf >= min_signal,
                "confidence": conf,
                "result": _condition_result(conf >= min_signal, True, conf, min_signal),
            })
        elif target_type in {"death_respawn", "death_missing_respawn", "death_only"}:
            conf = float(observed.get("death_notification_confidence", 0.0) or 0.0)
            optional_absent = bool(rules.get("death_notification_optional_if_absent", False))
            is_detected = conf >= min_signal
            conditions.append({
                "condition": "death_notification_detected",
                "expected": True,
                "observed": is_detected,
                "confidence": conf,
                "result": (
                    _condition_result(is_detected, True, conf, min_signal)
                    if is_detected or not optional_absent
                    else "not_required"
                ),
                "note": (
                    "Death panel evidence is optional because that UI can be disabled by the player."
                    if optional_absent and not is_detected
                    else ""
                ),
            })

        conflict = _parse_spawn_conflict(notes)
        if conflict:
            conditions.append({
                "condition": "spawn_location_check",
                "expected": conflict["expected"],
                "observed": conflict["observed"],
                "confidence": conflict["confidence"],
                "result": "conflict",
                "note": (
                    "This condition is not required by current QA config."
                    if not rules.get("require_spawn_location_check", True)
                    else "Spawn location check is required by current QA config."
                ),
            })

    elif rule_id == "kill_count_increment":
        count_conf = float(observed.get("count_change_confidence", 0.0) or 0.0)
        notif_conf = float(observed.get("kill_notification_confidence", 0.0) or 0.0)
        conditions.append({
            "condition": "count_change_detected",
            "expected": bool(rules.get("require_count_change_after_kill_notification", True)),
            "observed": count_conf >= min_signal,
            "confidence": count_conf,
            "result": "satisfied" if count_conf >= min_signal else "not_required" if not rules.get("require_count_change_after_kill_notification", True) else "not_satisfied",
        })
        conditions.append({
            "condition": "kill_notification_detected",
            "expected": bool(rules.get("require_kill_notification_for_count_change", True)),
            "observed": notif_conf >= min_signal,
            "confidence": notif_conf,
            "result": "satisfied" if notif_conf >= min_signal else "not_required" if not rules.get("require_kill_notification_for_count_change", True) else "not_satisfied",
        })

    elif rule_id == "respawn_same_space":
        respawn_conf = float(observed.get("respawn_segment_confidence", observed.get("event_confidence", 0.0)) or 0.0)
        conditions.append({
            "condition": "respawn_segment_detected",
            "expected": True,
            "observed": finding.get("result") == "PASS",
            "confidence": respawn_conf,
            "result": "satisfied" if finding.get("result") == "PASS" else "not_satisfied",
            "note": (
                "Spawn location OCR/text check is disabled by current QA config."
                if not rules.get("require_spawn_location_check", True)
                else ""
            ),
        })

    return {
        "rule_id": rule_id,
        "rule_enabled": not ("rule_disabled" in notes),
        "input_event_ids": related_events,
        "conditions": conditions,
        "thresholds_used": {
            "pass_confidence": thresholds.get("pass_confidence"),
            "min_signal_confidence": thresholds.get("min_signal_confidence"),
            "spawn_pass_threshold": thresholds.get("spawn_pass_threshold"),
            "spawn_fail_threshold": thresholds.get("spawn_fail_threshold"),
        },
        "final_decision_reason": finding.get("reason", ""),
    }


def _trace_links(
    *,
    check_id: str,
    related_events: list[str],
    rule_id: str,
    target_event_id: str | None,
    video_reports: dict[str, str],
    global_lookup: dict[str, dict[str, Any]],
    evidence_report: dict[str, Any],
    out_root: Path,
) -> dict[str, Any]:
    global_event_id = related_events[0] if related_events else None
    global_event = global_lookup.get(global_event_id or "", {})
    raw_event_ids = list(global_event.get("linked_raw_event_ids", []) or [])
    module_map = {
        "notification_detector": "notifications",
        "game_state_classifier": "game_state",
        "respawn_segment_detector": "respawn",
        "spawn_location_recognizer": "spawn_location",
        "global_timeline": "global_timeline",
    }
    return {
        "check_id": check_id,
        "global_event_id": global_event_id,
        "raw_event_ids": raw_event_ids,
        "module_reports": {
            name: _relpath(video_reports.get(key), out_root)
            for name, key in module_map.items()
            if video_reports.get(key)
        },
        "evidence_items": _collect_evidence_item_paths(evidence_report, rule_id, target_event_id, out_root),
    }


def _build_qa_checks_and_evidence_index(
    *,
    qa_report: dict[str, Any],
    evidence_report: dict[str, Any],
    global_report: dict[str, Any],
    video_id: str,
    video_reports: dict[str, str],
    rule_cfg: dict[str, Any],
    out_root: Path,
    start_index: int,
    evidence_index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    checks: list[dict[str, Any]] = []
    uncertainty_causes: list[str] = []
    check_index = start_index
    global_lookup = _event_lookup(global_report, video_id)
    for result in qa_report.get("qa_results", []) or []:
        rule_id = str(result.get("rule_id", "unknown_rule"))
        findings = result.get("findings", []) or []
        if not findings:
            findings = [{
                "rule_id": rule_id,
                "objective": result.get("objective"),
                "result": result.get("result"),
                "confidence": result.get("confidence"),
                "reason": result.get("summary", ""),
                "evidence": {},
            }]
        for finding in findings:
            check_index += 1
            check_id = f"check_{check_index:04d}"
            target_event_id = finding.get("target_event_id")
            related_events = [f"{video_id}_{target_event_id}"] if target_event_id else []
            artifacts = _collect_evidence_artifacts(evidence_report, rule_id, target_event_id, out_root)
            evidence_paths = [a["path"] for a in artifacts if a.get("path")]
            notes = [str(n) for n in finding.get("notes", []) or []]
            if finding.get("result") in {"UNCERTAIN", "NEED_REVIEW"}:
                uncertainty_causes.extend(notes)

            check: dict[str, Any] = {
                "check_id": check_id,
                "video_id": video_id,
                "check_type": _check_type(rule_id),
                "result": finding.get("result", result.get("result", "UNKNOWN")),
                "severity": "critical" if rule_id == "respawn_same_space" else "major",
                "related_events": related_events,
                "rule": {
                    "description": finding.get("objective") or result.get("objective", ""),
                    "rule_id": rule_id,
                },
                "observed": finding.get("evidence", {}),
                "confidence": finding.get("confidence", result.get("confidence", 0.0)),
                "reason": finding.get("reason", ""),
                "evidence": evidence_paths,
            }
            check["decision_trace"] = _decision_trace(
                rule_id=rule_id,
                finding=finding,
                related_events=related_events,
                rule_cfg=rule_cfg,
            )
            check["trace_links"] = _trace_links(
                check_id=check_id,
                related_events=related_events,
                rule_id=rule_id,
                target_event_id=target_event_id,
                video_reports=video_reports,
                global_lookup=global_lookup,
                evidence_report=evidence_report,
                out_root=out_root,
            )
            if finding.get("time") is not None:
                t = float(finding["time"])
                check["time_range_sec"] = [t, t]
            if notes:
                check["notes"] = notes
            if check["result"] in {"UNCERTAIN", "NEED_REVIEW"}:
                check["uncertainty"] = {
                    "type": "INSUFFICIENT_EVIDENCE" if check["result"] == "UNCERTAIN" else "SIGNAL_CONFLICT",
                    "primary_cause": notes[0] if notes else check["result"],
                    "recommended_action": "MANUAL_REVIEW",
                }
            checks.append(check)

            for artifact in artifacts:
                path = artifact.get("path")
                if not path:
                    continue
                indexed = evidence_index.setdefault(path, {
                    "path": path,
                    "type": artifact.get("type"),
                    "related_events": [],
                    "related_checks": [],
                })
                for event_id in related_events:
                    if event_id not in indexed["related_events"]:
                        indexed["related_events"].append(event_id)
                if check_id not in indexed["related_checks"]:
                    indexed["related_checks"].append(check_id)
    return checks, check_index, uncertainty_causes


def build_sample_style_final_report(
    *,
    dataset: Path,
    out_root: Path,
    args: argparse.Namespace,
    video_results: list[dict[str, Any]],
) -> dict[str, Any]:
    input_videos: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    qa_checks: list[dict[str, Any]] = []
    evidence_index: dict[str, dict[str, Any]] = {}
    uncertainty_causes: list[str] = []
    check_index = 0
    pipeline_manifests: list[dict[str, Any]] = []
    rule_cfg = _rule_config(args)

    for idx, video_result in enumerate(video_results, start=1):
        video_path = Path(str(video_result.get("video", "")))
        video_id = f"video_{idx:04d}_{_safe_name(video_path)}"
        output_dir = Path(str(video_result.get("output_dir", "")))
        input_videos.append({
            "video_id": video_id,
            "video_path": str(video_path),
            "role": video_result.get("video_name", video_path.name),
            "resolution": args.resize,
            "fps": None,
            "pipeline_status": video_result.get("status"),
        })
        pipeline_manifests.append({
            "video_id": video_id,
            "status": video_result.get("status"),
            "manifest_path": _relpath(str(output_dir / "pipeline_manifest.json"), out_root),
            "output_dir": _relpath(str(output_dir), out_root),
            "reports": {
                key: _relpath(value, out_root)
                for key, value in (video_result.get("reports", {}) or {}).items()
            },
        })

        reports = video_result.get("reports", {}) or {}
        global_report = _json_load(Path(reports.get("global_timeline", ""))) or {}
        qa_report = _json_load(Path(reports.get("qa_rules", ""))) or {}
        evidence_report = _json_load(Path(reports.get("evidence", ""))) or {}

        for global_event in global_report.get("global_events", []) or []:
            events.append(_event_from_global_event(global_event, video_id))

        new_checks, check_index, causes = _build_qa_checks_and_evidence_index(
            qa_report=qa_report,
            evidence_report=evidence_report,
            global_report=global_report,
            video_id=video_id,
            video_reports=reports,
            rule_cfg=rule_cfg,
            out_root=out_root,
            start_index=check_index,
            evidence_index=evidence_index,
        )
        qa_checks.extend(new_checks)
        uncertainty_causes.extend(causes)

    result_counts: dict[str, int] = {}
    by_check_type: dict[str, dict[str, int]] = {}
    for check in qa_checks:
        result = str(check.get("result", "UNKNOWN"))
        check_type = str(check.get("check_type", "unknown"))
        result_counts[result] = result_counts.get(result, 0) + 1
        by_check_type.setdefault(check_type, {"PASS": 0, "FAIL": 0, "UNCERTAIN": 0, "NEED_REVIEW": 0})
        by_check_type[check_type][result] = by_check_type[check_type].get(result, 0) + 1

    event_status_counts: dict[str, int] = {}
    for event in events:
        status = str(event.get("status", "UNKNOWN"))
        event_status_counts[status] = event_status_counts.get(status, 0) + 1

    return {
        "schema_version": "1.0",
        "report_type": "crossfire_video_only_qa_evidence_report",
        "game": "CrossFire",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Generated deterministically from detector, timeline, QA rule, and evidence reports. No LLM is used for PASS/FAIL decisions.",
        "input_videos": input_videos,
        "analysis_config": {
            "pipeline": "video_only",
            "sampling_strategy": {
                "pass_1_fps": args.sample_fps,
                "pass_2_fps": args.spawn_sample_fps,
                "refine_window_sec": None,
            },
            "roi_layout": _roi_layout(args),
            "rules": _rule_config(args),
            "run_reproducibility": _run_reproducibility(args, out_root),
            "pipeline_options": {
                "dataset": str(dataset.resolve()),
                "output_dir": str(out_root),
                "resize": args.resize,
                "base_resolution": args.base_resolution,
                "expected_spawn": args.expected_spawn,
                "spawn_references": args.spawn_references,
                "score_backend": args.score_backend,
            },
            "pipeline_manifests": pipeline_manifests,
        },
        "overall_result": _overall_from_counts(result_counts),
        "summary": {
            "total_events": len(events),
            "total_checks": len(qa_checks),
            "result_counts": result_counts,
            "event_status_counts": event_status_counts,
            "by_check_type": by_check_type,
            "primary_uncertainty_causes": sorted(set(uncertainty_causes)),
        },
        "events": events,
        "qa_checks": qa_checks,
        "uncertainty_policy": {
            "event_statuses": ["CONFIRMED", "INFERRED", "UNCERTAIN", "MISSING", "CONFLICT"],
            "qa_results": ["PASS", "FAIL", "UNCERTAIN", "NEED_REVIEW"],
            "principles": [
                "Detector failure is not automatically treated as a game QA failure.",
                "Use UNCERTAIN when the detector cannot make a confident judgment.",
                "Use NEED_REVIEW when strong signals conflict.",
                "Use FAIL only when screen evidence confidently violates an expected rule.",
            ],
        },
        "evidence_index": list(evidence_index.values()),
        "run_reproducibility": _run_reproducibility(args, out_root),
    }


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    summary = report.get("summary", {})
    lines.append("# CrossFire Dataset QA Final Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for key, value in summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.append("")
    lines.append(f"- **overall_result**: {report.get('overall_result')}")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| check_id | video_id | type | result | confidence | reason |")
    lines.append("|---|---|---|---|---:|---|")
    for check in report.get("qa_checks", []):
        confidence = check.get("confidence", 0.0)
        try:
            confidence_str = f"{float(confidence):.3f}"
        except Exception:
            confidence_str = ""
        lines.append(
            f"| {check.get('check_id')} | {check.get('video_id')} | {check.get('check_type')} | "
            f"{check.get('result')} | {confidence_str} | {check.get('reason', '')} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_video(video: Path, out_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    video_name = _safe_name(video)
    video_out = out_root / video_name
    video_out.mkdir(parents=True, exist_ok=True)
    video_summary: dict[str, Any] = {
        "video": str(video),
        "video_name": video.name,
        "output_dir": str(video_out),
        "status": "RUNNING",
        "stages": [],
    }

    stages_ok = True
    for name, cmd, cwd in build_stage_commands(video, video_out, args):
        ok = run_stage(name=name, cmd=cmd, cwd=cwd, video_summary=video_summary, args=args)
        if not ok:
            stages_ok = False
            break

    video_summary.update(summarize_video(video, video_out, stages_ok))
    video_summary["output_dir"] = str(video_out)
    _json_dump(video_summary, video_out / "pipeline_manifest.json")
    return video_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full CrossFire QA pipeline for a video dataset.")
    parser.add_argument("--dataset", required=True, help="Directory containing videos, or a single video file.")
    parser.add_argument("--out", default=str(ROOT_DIR / "outputs" / "final_qa"), help="Output root directory.")
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--spawn-sample-fps", type=float, default=10.0)
    parser.add_argument("--max-spawn-checks", type=int, default=0, help="Limit spawn location checks per video; 0 means all detected respawns.")
    parser.add_argument("--resize", default="1920x1080")
    parser.add_argument("--base-resolution", default="1920x1080")
    parser.add_argument("--max-frames", type=int, default=None)

    parser.add_argument("--roi-config", default=_default_config("roi_config.example.json"))
    parser.add_argument("--score-config", default=_default_config("score_reader_config.example.json"))
    parser.add_argument("--notification-config", default=_default_config("notification_config.example.json"))
    parser.add_argument("--game-state-config", default=_default_config("game_state_config.example.json"))
    parser.add_argument("--respawn-config", default=_default_config("respawn_config.example.json"))
    parser.add_argument("--spawn-config", default=_default_config("spawn_location_config.example.json"))
    parser.add_argument("--global-config", default=_default_config("global_temporal_config.example.json"))
    parser.add_argument("--qa-config", default=_default_config("qa_rule_config.example.json"))
    parser.add_argument("--evidence-config", default=_default_config("evidence_report_config.example.json"))

    parser.add_argument("--ui-templates", default=None)
    parser.add_argument("--digit-templates", default=None)
    parser.add_argument("--notification-templates", default=None)
    parser.add_argument("--state-templates", default=None)
    parser.add_argument("--spawn-references", default=None)
    parser.add_argument("--expected-spawn", default=None, help="Expected spawn label, e.g. 'BL Base'.")

    parser.add_argument("--score-backend", default="template", choices=["auto", "template", "easyocr", "paddleocr", "vlm"])
    parser.add_argument("--score-sample-interval-sec", type=float, default=0.0)
    parser.add_argument("--min-digit-confidence", type=float, default=0.55)
    parser.add_argument("--easyocr-model-dir", default=str(ROOT_DIR / "outputs" / "easyocr_models"))
    parser.add_argument("--vlm-api-key-file", default=None)
    parser.add_argument("--vlm-model", default="gpt-4o-mini")
    parser.add_argument("--vlm-base-url", default="https://api.openai.com/v1")
    parser.add_argument("--vlm-sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--install-easyocr", action="store_true")
    parser.add_argument("--disable-easyocr", action="store_true")

    parser.add_argument("--save-overlays", action="store_true")
    parser.add_argument("--save-debug-crops", action="store_true")
    parser.add_argument("--skip-spawn-location", action="store_true", help="write an empty spawn report instead of running spawn OCR/location checks")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--no-template-verify", action="store_true")
    parser.add_argument("--no-heuristics", action="store_true")
    parser.add_argument("--keep-going", action="store_true", help="Continue with the next video when a stage fails.")
    parser.add_argument("--dry-run", action="store_true", help="Write command manifests without executing stages.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = Path(args.dataset).expanduser()
    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    videos = discover_videos(dataset)
    if not videos:
        raise SystemExit(f"No videos found under: {dataset}")

    print(f"Found {len(videos)} video(s). Output: {out_root}", flush=True)
    results: list[dict[str, Any]] = []
    for video in videos:
        print(f"\n=== {video.name} ===", flush=True)
        results.append(run_video(video, out_root, args))

    final_report = build_sample_style_final_report(
        dataset=dataset,
        out_root=out_root,
        args=args,
        video_results=results,
    )
    final_json = out_root / "final_report.json"
    final_md = out_root / "final_report.md"
    _json_dump(final_report, final_json)
    write_markdown_report(final_report, final_md)

    print(json.dumps({
        "final_report": str(final_json),
        "markdown_report": str(final_md),
        "overall_result": final_report.get("overall_result"),
        "summary": final_report["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
