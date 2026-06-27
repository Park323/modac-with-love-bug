from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


EXCLUDED_MODULE_REPORTS = {"spawn_location_recognizer"}

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
    for check in report["checks"]:
        packaged = dict(check)
        packaged["packaged_module_reports"] = package_module_reports(final_root, package_root, check)
        packaged["packaged_evidence_json"] = package_evidence_json(final_root, package_root, check)
        packaged.pop("source_video_path", None)
        checks.append(packaged)

        write_json(package_root / "data" / "checks" / f"{check['check_id']}.json", packaged)

    return {
        "package_type": "crossfire_qa_review_package",
        "schema_version": "1.0",
        "purpose": "Handoff package for building a QA visualization page. PASS checks and synthetic cases that passed are excluded.",
        "source_final_report": str((final_root / "final_report.json").resolve()),
        "overall_result": report.get("overall_result"),
        "summary": report.get("summary", {}),
        "review_summary": report.get("web_summary", {}),
        "run_reproducibility": report.get("run_reproducibility", {}),
        "included_results": ["FAIL", "UNCERTAIN", "NEED_REVIEW"],
        "excluded_results": ["PASS"],
        "checks": checks,
    }


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
        packed["artifacts"] = artifacts
        packed["packaged_module_reports"] = package_module_reports(final_root, package_root, check)
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
- `reports/<check_id>/`: copied module/evidence JSON needed for drill-down.

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
    args = parser.parse_args()

    final_root = Path(args.run_dir).expanduser().resolve()
    package_root = Path(args.out).expanduser().resolve()
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
