from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_report_generator import EvidenceReportGenerator, load_evidence_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate final CrossFire QA evidence report.")
    parser.add_argument("--qa-rule-report", required=True, help="Path to qa_rule_report.json")
    parser.add_argument("--global-timeline", required=True, help="Path to global_event_timeline.json")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--video", default=None, help="Optional source MP4 for full-frame snapshots and evidence clips")
    parser.add_argument("--ui-report", default=None, help="Optional ui_detection_report.json for ROI crop paths / bboxes")
    parser.add_argument("--evidence-config", default=None, help="Optional evidence report config JSON")
    parser.add_argument("--no-markdown", action="store_true", help="Do not write report.md")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_evidence_config(args.evidence_config)
    generator = EvidenceReportGenerator(config=config)

    qa_report = EvidenceReportGenerator.load_json(args.qa_rule_report)
    global_timeline = EvidenceReportGenerator.load_json(args.global_timeline)
    ui_report = EvidenceReportGenerator.load_json(args.ui_report) if args.ui_report else None

    assert qa_report is not None
    assert global_timeline is not None

    input_reports = {
        "qa_rule_report": args.qa_rule_report,
        "global_timeline": args.global_timeline,
        "ui_report": args.ui_report,
    }

    report = generator.generate(
        qa_report=qa_report,
        global_timeline=global_timeline,
        output_dir=out_dir,
        video_path=args.video,
        ui_report=ui_report,
        ui_report_path=args.ui_report,
        input_reports=input_reports,
        write_markdown=not args.no_markdown,
    )

    print(json.dumps({
        "report_path": str(out_dir / "report.json"),
        "markdown_path": None if args.no_markdown else str(out_dir / "report.md"),
        "evidence_dir": str(out_dir / "evidence"),
        "summary": report.get("summary", {}),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
