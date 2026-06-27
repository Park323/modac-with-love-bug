from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from qa_rule_engine import QARuleEngine, load_qa_rule_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QA rule evaluation on a CrossFire global event timeline.")
    parser.add_argument("--global-timeline", required=True, help="Path to global_event_timeline.json")
    parser.add_argument("--qa-config", default=None, help="Optional QA rule config JSON")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--pretty", action="store_true", help="Write markdown summary")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_qa_rule_config(args.qa_config)
    engine = QARuleEngine(config=config)
    timeline = QARuleEngine.load_json(args.global_timeline)
    report = engine.evaluate(timeline)
    report["source_global_timeline"] = args.global_timeline

    report_path = out_dir / "qa_rule_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md_path = None
    if args.pretty:
        md_path = out_dir / "qa_rule_report.md"
        write_markdown_report(report, md_path)

    print(json.dumps({
        "report_path": str(report_path),
        "markdown_path": str(md_path) if md_path else None,
        "summary": report.get("summary", {}),
        "out_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))


def write_markdown_report(report: dict[str, Any], path: Path) -> None:
    summary = report.get("summary", {})
    lines: list[str] = []
    lines.append("# QA Rule Report")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append(f"- **overall_result**: {summary.get('overall_result')}")
    lines.append(f"- **overall_confidence**: {float(summary.get('overall_confidence', 0.0)):.3f}")
    lines.append(f"- **num_findings**: {summary.get('num_findings')}")
    lines.append("")
    lines.append("## Objective Results")
    lines.append("")
    lines.append("| rule_id | result | confidence | pass | fail | uncertain | need_review | summary |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for obj in report.get("qa_results", []) or []:
        lines.append(
            f"| {obj.get('rule_id')} | {obj.get('result')} | {float(obj.get('confidence', 0.0)):.3f} | "
            f"{obj.get('pass_count', 0)} | {obj.get('fail_count', 0)} | {obj.get('uncertain_count', 0)} | "
            f"{obj.get('need_review_count', 0)} | {obj.get('summary', '')} |"
        )
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    for obj in report.get("qa_results", []) or []:
        lines.append(f"### {obj.get('rule_id')} — {obj.get('result')}")
        lines.append("")
        for f in obj.get("findings", []) or []:
            time = f.get("time")
            time_str = "" if time is None else f"{float(time):.3f}s"
            lines.append(f"- **{f.get('result')}** `{f.get('target_event_id') or 'no_event'}` {time_str}: {f.get('reason')} ")
            notes = f.get("notes", []) or []
            if notes:
                lines.append(f"  - notes: {', '.join(str(n) for n in notes)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
