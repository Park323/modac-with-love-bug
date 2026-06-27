from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


EXCLUDED_MODULE_REPORTS = {"spawn_location_recognizer"}
DEFAULT_CONTEXT_CROP_FRAMES = 1
STAGE_REPORTS = {
    "ui_detector": ("01_ui", "ui_detection_report.json"),
    "kill_count_reader": ("02_kill_count", "kill_count_report.json"),
    "notification_detector": ("03_notifications", "notification_report.json"),
    "game_state_classifier": ("04_game_state", "game_state_report.json"),
    "respawn_segment_detector": ("05_respawn", "respawn_segment_report.json"),
    "global_timeline": ("07_global_timeline", "global_event_timeline.json"),
    "qa_rule_engine": ("08_qa_rules", "qa_rule_report.json"),
    "evidence_report_generator": ("09_evidence_report", "report.json"),
}

from build_qa_web_report import (
    build_report,
    collect_existing_artifacts,
    load_first_evidence_item,
    load_json,
    make_clip,
    make_thumbnail,
    parse_time_from_check,
    safe_name,
    write_json,
)


def copy_if_exists(src: Path, dst: Path) -> str | None:
    if not src.exists() or not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst.as_posix()


def rel_to_package(path: Path, package_root: Path) -> str:
    return path.resolve().relative_to(package_root.resolve()).as_posix()


def infer_video_output_dir(final_root: Path, check: dict[str, Any]) -> Path | None:
    module_reports = (check.get("trace_links") or {}).get("module_reports", {})
    for rel_path in module_reports.values():
        parts = Path(str(rel_path)).parts
        if not parts:
            continue
        candidate = final_root / parts[0]
        if candidate.exists():
            return candidate
    return None


def parse_crop_dir_time(path: Path) -> float | None:
    match = re.search(r"_t([0-9]+(?:\.[0-9]+)?)", path.name)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def nearest_crop_dirs(video_out_dir: Path, focus_time: float | None, limit: int) -> list[Path]:
    crop_root = video_out_dir / "01_ui" / "crops"
    if limit <= 0 or not crop_root.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for path in crop_root.iterdir():
        if not path.is_dir():
            continue
        crop_time = parse_crop_dir_time(path)
        if crop_time is None:
            continue
        delta = abs(crop_time - float(focus_time or crop_time))
        candidates.append((delta, path))
    return [path for _, path in sorted(candidates, key=lambda item: (item[0], item[1].name))[:limit]]


def package_context_crops(
    *,
    final_root: Path,
    package_root: Path,
    check: dict[str, Any],
    focus_time: float | None,
    max_frames: int = DEFAULT_CONTEXT_CROP_FRAMES,
) -> list[dict[str, str]]:
    video_out_dir = infer_video_output_dir(final_root, check)
    if not video_out_dir:
        return []

    copied: list[dict[str, str]] = []
    check_id = str(check["check_id"])
    for crop_dir in nearest_crop_dirs(video_out_dir, focus_time, max_frames):
        dst_dir = package_root / "assets" / check_id / "crops" / safe_name(crop_dir.name)
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(crop_dir.iterdir()):
            if not src.is_file() or src.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            dst = dst_dir / safe_name(src.name)
            shutil.copy2(src, dst)
            copied.append(
                {
                    "kind": f"context_crop:{src.stem}",
                    "type": "image",
                    "path": rel_to_package(dst, package_root),
                }
            )
    return copied


def package_stage_reports(final_root: Path, package_root: Path, check: dict[str, Any]) -> dict[str, str]:
    video_out_dir = infer_video_output_dir(final_root, check)
    if not video_out_dir:
        return {}

    packaged: dict[str, str] = {}
    check_id = str(check["check_id"])
    for stage_name, (stage_dir, file_name) in STAGE_REPORTS.items():
        src = video_out_dir / stage_dir / file_name
        dst = package_root / "reports" / check_id / "stages" / f"{safe_name(stage_name)}.json"
        copied = copy_if_exists(src, dst)
        if copied:
            packaged[stage_name] = rel_to_package(dst, package_root)

    pipeline_src = video_out_dir / "pipeline_manifest.json"
    pipeline_dst = package_root / "reports" / check_id / "stages" / "pipeline_manifest.json"
    copied = copy_if_exists(pipeline_src, pipeline_dst)
    if copied:
        packaged["pipeline_manifest"] = rel_to_package(pipeline_dst, package_root)
    return packaged


def package_module_reports(final_root: Path, package_root: Path, check: dict[str, Any]) -> dict[str, str]:
    packaged: dict[str, str] = {}
    check_id = check["check_id"]
    module_reports = (check.get("trace_links") or {}).get("module_reports", {})
    for module_name, rel_path in module_reports.items():
        if module_name in EXCLUDED_MODULE_REPORTS:
            continue
        src = final_root / rel_path
        dst = package_root / "reports" / check_id / f"{safe_name(module_name)}.json"
        copied = copy_if_exists(src, dst)
        if copied:
            packaged[module_name] = dst.relative_to(package_root).as_posix()
    return packaged


def package_evidence_json(final_root: Path, package_root: Path, check: dict[str, Any]) -> list[str]:
    copied_paths: list[str] = []
    check_id = check["check_id"]
    for rel_path in check.get("evidence", []) or []:
        src = final_root / rel_path
        if src.suffix.lower() != ".json":
            continue
        dst = package_root / "reports" / check_id / "evidence" / safe_name(src.name)
        copied = copy_if_exists(src, dst)
        if copied:
            copied_paths.append(dst.relative_to(package_root).as_posix())
    return copied_paths


def prune_for_handoff(report: dict[str, Any], final_root: Path, package_root: Path) -> dict[str, Any]:
    checks = []
    excluded_checks = []
    for check in report["checks"]:
        if is_unobservable_video_tail_respawn(check, package_root):
            excluded_checks.append(
                {
                    "check_id": check.get("check_id"),
                    "result": check.get("result"),
                    "check_type": check.get("check_type"),
                    "reason": check.get("reason"),
                    "exclusion_reason": "respawn_missing_at_video_tail_no_later_segment",
                }
            )
            cleanup_packaged_check(package_root, str(check.get("check_id")))
            continue
        packaged = dict(check)
        context_crops = package_context_crops(
            final_root=final_root,
            package_root=package_root,
            check=check,
            focus_time=check.get("focus_time_sec"),
        )
        if context_crops:
            packaged.setdefault("artifacts", []).extend(context_crops)
        packaged["packaged_module_reports"] = package_module_reports(final_root, package_root, check)
        packaged["packaged_stage_reports"] = package_stage_reports(final_root, package_root, check)
        packaged["packaged_evidence_json"] = package_evidence_json(final_root, package_root, check)
        packaged.pop("source_video_path", None)
        checks.append(packaged)

        write_json(package_root / "data" / "checks" / f"{check['check_id']}.json", packaged)

    result_counts = Counter(c["result"] for c in checks)
    type_counts: dict[str, Counter] = defaultdict(Counter)
    case_counts: dict[str, Counter] = defaultdict(Counter)
    for c in checks:
        type_counts[str(c.get("check_type", "unknown"))][str(c.get("result", "unknown"))] += 1
        case_counts[str(c.get("case_name", "unknown"))][str(c.get("result", "unknown"))] += 1
    review_summary = dict(report.get("web_summary", {}))
    review_summary.update(
        {
            "shown_checks": len(checks),
            "shown_result_counts": dict(result_counts),
            "shown_by_check_type": {k: dict(v) for k, v in sorted(type_counts.items())},
            "shown_by_case": {k: dict(v) for k, v in sorted(case_counts.items())},
            "excluded_checks_count": len(excluded_checks),
        }
    )
    return {
        "package_type": "crossfire_qa_review_package",
        "schema_version": "1.0",
        "purpose": "Handoff package for building a QA visualization page. PASS checks and synthetic cases that passed are excluded from the main issue queue.",
        "source_final_report": str((final_root / "final_report.json").resolve()),
        "overall_result": report.get("overall_result"),
        "summary": report.get("summary", {}),
        "review_summary": review_summary,
        "run_reproducibility": report.get("run_reproducibility", {}),
        "included_results": ["FAIL", "UNCERTAIN", "NEED_REVIEW"],
        "excluded_results": ["PASS"],
        "excluded_checks": excluded_checks,
        "checks": checks,
    }


def cleanup_packaged_check(package_root: Path, check_id: str) -> None:
    if not check_id:
        return
    for path in [
        package_root / "assets" / check_id,
        package_root / "reports" / check_id,
        package_root / "data" / "checks" / f"{check_id}.json",
        package_root / "data" / "pass_examples" / f"{check_id}.json",
    ]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def has_no_later_segment(value: Any) -> bool:
    if isinstance(value, dict):
        notes = value.get("notes")
        if isinstance(notes, list) and "no_later_segment" in notes:
            return True
        return any(has_no_later_segment(v) for v in value.values())
    if isinstance(value, list):
        return any(has_no_later_segment(v) for v in value)
    return False


def is_unobservable_video_tail_respawn(check: dict[str, Any], package_root: Path) -> bool:
    if check.get("result") != "FAIL" or check.get("check_type") != "respawn_operation_after_death":
        return False
    if "no respawn segment was found" not in str(check.get("reason", "")).lower():
        return False
    for artifact in check.get("artifacts", []) or []:
        if artifact.get("type") != "json":
            continue
        path = package_root / str(artifact.get("path", ""))
        if not path.exists():
            continue
        try:
            evidence_item = load_json(path)
        except Exception:
            continue
        if has_no_later_segment(evidence_item):
            return True
    return False


def case_from_video_path(video_path: str | None) -> str | None:
    if not video_path:
        return None
    return Path(video_path).parent.name


def compact_check(check: dict[str, Any], videos_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    video = videos_by_id.get(check.get("video_id"), {})
    decision_trace = check.get("decision_trace") or {}
    trace_links = dict(check.get("trace_links", {}) or {})
    module_reports = dict(trace_links.get("module_reports", {}) or {})
    for name in EXCLUDED_MODULE_REPORTS:
        module_reports.pop(name, None)
    trace_links["module_reports"] = module_reports
    return {
        "check_id": check.get("check_id"),
        "video_id": check.get("video_id"),
        "video_name": Path(video.get("video_path", check.get("video_id", ""))).name,
        "case_name": case_from_video_path(video.get("video_path")),
        "check_type": check.get("check_type"),
        "result": check.get("result"),
        "severity": check.get("severity"),
        "confidence": check.get("confidence"),
        "reason": check.get("reason"),
        "rule": check.get("rule", {}),
        "observed": check.get("observed", {}),
        "decision_trace": {
            "rule_id": decision_trace.get("rule_id"),
            "rule_enabled": decision_trace.get("rule_enabled"),
            "conditions": decision_trace.get("conditions", []),
            "thresholds_used": decision_trace.get("thresholds_used", {}),
            "final_decision_reason": decision_trace.get("final_decision_reason"),
        },
        "trace_links": trace_links,
        "related_events": check.get("related_events", []),
        "time_range_sec": check.get("time_range_sec"),
        "notes": check.get("notes", []),
    }


def select_pass_examples(pass_checks: list[dict[str, Any]], limit_per_type: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for check in pass_checks:
        key = str(check.get("check_type", "unknown"))
        if counts.get(key, 0) >= limit_per_type:
            continue
        selected.append(check)
        counts[key] = counts.get(key, 0) + 1
    return selected


def add_pass_materials(
    *,
    final_root: Path,
    package_root: Path,
    pass_examples: list[dict[str, Any]],
    videos_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for check in pass_examples:
        packed = compact_check(check, videos_by_id)
        check_id = str(check["check_id"])
        video = videos_by_id.get(check.get("video_id"), {})
        video_path = Path(video.get("video_path", ""))
        evidence_item = load_first_evidence_item(final_root, check)
        artifacts = collect_existing_artifacts(
            final_root=final_root,
            web_root=package_root,
            check_id=check_id,
            check=check,
            evidence_item=evidence_item,
        )
        focus_time = parse_time_from_check(check, evidence_item, video_path)
        if video_path.exists():
            if not any(a["type"] == "image" for a in artifacts):
                thumb = make_thumbnail(
                    video_path=video_path,
                    dst_root=package_root,
                    final_root=package_root,
                    check_id=check_id,
                    time_sec=focus_time,
                    result=check.get("result", ""),
                    check_type=check.get("check_type", ""),
                )
                if thumb:
                    artifacts.insert(0, {"kind": "generated_thumbnail", "type": "image", "path": thumb})
            if not any(a["type"] == "video" for a in artifacts):
                clip = make_clip(
                    video_path=video_path,
                    dst_root=package_root,
                    final_root=package_root,
                    check_id=check_id,
                    center_sec=focus_time,
                    result=check.get("result", ""),
                    check_type=check.get("check_type", ""),
                )
                if clip:
                    artifacts.append({"kind": "generated_clip", "type": "video", "path": clip})
        packed["focus_time_sec"] = focus_time
        context_crops = package_context_crops(
            final_root=final_root,
            package_root=package_root,
            check=check,
            focus_time=focus_time,
        )
        if context_crops:
            artifacts.extend(context_crops)
        packed["artifacts"] = artifacts
        packed["packaged_module_reports"] = package_module_reports(final_root, package_root, check)
        packed["packaged_stage_reports"] = package_stage_reports(final_root, package_root, check)
        packed["packaged_evidence_json"] = package_evidence_json(final_root, package_root, check)
        out.append(packed)
        write_json(package_root / "data" / "pass_examples" / f"{check_id}.json", packed)
    return out


def add_pass_details(
    *,
    final_root: Path,
    package_root: Path,
    package: dict[str, Any],
    include_pass_details: bool,
    pass_sample_limit_per_type: int,
) -> None:
    final_report = load_json(final_root / "final_report.json")
    videos_by_id = {v["video_id"]: v for v in final_report.get("input_videos", [])}
    pass_checks = [c for c in final_report.get("qa_checks", []) if c.get("result") == "PASS"]
    if include_pass_details:
        pass_details = [compact_check(c, videos_by_id) for c in pass_checks]
        write_json(package_root / "data" / "pass_checks.json", pass_details)
        package["pass_checks_path"] = "data/pass_checks.json"
        package["pass_checks_count"] = len(pass_details)
    else:
        package["pass_checks_count"] = len(pass_checks)

    pass_examples = select_pass_examples(pass_checks, pass_sample_limit_per_type)
    packed_examples = add_pass_materials(
        final_root=final_root,
        package_root=package_root,
        pass_examples=pass_examples,
        videos_by_id=videos_by_id,
    )
    package["pass_examples"] = packed_examples
    package["pass_examples_count"] = len(packed_examples)


README = """# CrossFire QA Review Package

This folder is a curated handoff package for building a QA visualization page.

The main `checks` list focuses on FAIL, UNCERTAIN, and NEED_REVIEW checks. PASS
checks are available separately so a frontend can also explain why normal cases
passed without mixing them into the main issue queue.

## Structure

- `package_manifest.json`: main entry point for the frontend.
- `data/checks/*.json`: one JSON file per non-PASS QA check.
- `data/pass_checks.json`: compact reasons/decision traces for PASS checks.
- `data/pass_examples/*.json`: representative PASS checks with media.
- `assets/<check_id>/`: generated or copied thumbnails, clips, full frames, and ROI images.
- `assets/<check_id>/crops/`: UI ROI crops from the frame nearest to the check time.
- `reports/<check_id>/`: copied module/evidence JSON needed for drill-down.
- `reports/<check_id>/stages/`: selected 01-09 pipeline stage reports for the same video.

## Frontend Notes

Use `package_manifest.json` as the source of truth. Media paths inside
`artifacts` are relative to this package root. Render `checks` as the issue queue
and use `pass_checks_path` / `pass_examples` for PASS explanation views.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a self-contained QA review package for non-PASS checks.")
    parser.add_argument("--run-dir", required=True, help="Pipeline output directory containing final_report.json.")
    parser.add_argument("--out", required=True, help="Output package directory.")
    parser.add_argument("--include-pass-details", action="store_true", help="Write compact PASS check explanations to data/pass_checks.json.")
    parser.add_argument("--pass-sample-limit-per-type", type=int, default=3, help="Representative PASS checks with media per check type.")
    parser.add_argument("--clean", action="store_true", help="Delete the output package directory before rebuilding it.")
    args = parser.parse_args()

    final_root = Path(args.run_dir).expanduser().resolve()
    package_root = Path(args.out).expanduser().resolve()
    if args.clean and package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True, exist_ok=True)

    report = build_report(final_root, package_root)
    package = prune_for_handoff(report, final_root, package_root)
    add_pass_details(
        final_root=final_root,
        package_root=package_root,
        package=package,
        include_pass_details=args.include_pass_details,
        pass_sample_limit_per_type=args.pass_sample_limit_per_type,
    )
    write_json(package_root / "package_manifest.json", package)
    (package_root / "README.md").write_text(README, encoding="utf-8")

    print(json.dumps({
        "package": str(package_root),
        "manifest": str(package_root / "package_manifest.json"),
        "num_checks": len(package["checks"]),
        "pass_checks_count": package.get("pass_checks_count"),
        "pass_examples_count": package.get("pass_examples_count"),
        "result_counts": package["review_summary"].get("shown_result_counts", {}),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
