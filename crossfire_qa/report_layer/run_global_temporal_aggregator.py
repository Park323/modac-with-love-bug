from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from global_temporal_aggregator import GlobalTemporalAggregator, load_global_temporal_config


def load_json(path: Optional[str]) -> Optional[dict[str, Any]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Report not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build global event timeline from CrossFire QA module reports.")
    parser.add_argument("--kill-count-report", default=None, help="Path to kill_count_report.json")
    parser.add_argument("--notification-report", default=None, help="Path to notification_report.json")
    parser.add_argument("--game-state-report", default=None, help="Path to game_state_report.json")
    parser.add_argument("--respawn-report", default=None, help="Path to respawn_segment_report.json")
    parser.add_argument("--spawn-location-report", default=None, help="Path to spawn_location_report.json")
    parser.add_argument("--global-config", default=None, help="Optional global temporal aggregation config JSON")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--pretty", action="store_true", help="Write pretty-formatted timeline markdown summary")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_global_temporal_config(args.global_config)
    aggregator = GlobalTemporalAggregator(config=config)

    reports = {
        "kill_count_report": load_json(args.kill_count_report),
        "notification_report": load_json(args.notification_report),
        "game_state_report": load_json(args.game_state_report),
        "respawn_report": load_json(args.respawn_report),
        "spawn_location_report": load_json(args.spawn_location_report),
    }

    report = aggregator.aggregate(**reports)
    report["source_reports"] = {
        "kill_count_report": args.kill_count_report,
        "notification_report": args.notification_report,
        "game_state_report": args.game_state_report,
        "respawn_report": args.respawn_report,
        "spawn_location_report": args.spawn_location_report,
    }

    report_path = out_dir / "global_event_timeline.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md_path = None
    if args.pretty:
        md_path = out_dir / "global_event_timeline.md"
        write_markdown_summary(report, md_path)

    print(json.dumps({
        "report_path": str(report_path),
        "markdown_path": str(md_path) if md_path else None,
        "summary": report.get("summary", {}),
        "out_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))


def write_markdown_summary(report: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    summary = report.get("summary", {})
    lines.append("# Global Event Timeline")
    lines.append("")
    lines.append("## Summary")
    for key, value in summary.items():
        lines.append(f"- **{key}**: {value}")
    lines.append("")
    lines.append("## Events")
    lines.append("")
    lines.append("| event_id | type | time | end_time | status | confidence | linked raw events |")
    lines.append("|---|---:|---:|---:|---|---:|---:|")
    for ev in report.get("global_events", []):
        time = f"{float(ev.get('time', 0.0)):.3f}"
        end = ev.get("end_time")
        end_str = "" if end is None else f"{float(end):.3f}"
        lines.append(
            f"| {ev.get('event_id')} | {ev.get('event_type')} | {time} | {end_str} | "
            f"{ev.get('status')} | {float(ev.get('confidence', 0.0)):.3f} | {len(ev.get('linked_raw_event_ids', []))} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
