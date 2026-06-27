from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write an empty spawn-location report for skipped spawn checks.")
    parser.add_argument("--respawn-report", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--reason", default="spawn_location_stage_skipped")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "spawn_location_report.json"

    report = {
        "source": {
            "type": "skipped",
            "respawn_report": args.respawn_report,
            "reason": args.reason,
        },
        "config": {},
        "summary": {
            "num_respawn_events": 0,
            "num_spawn_checks": 0,
            "num_pass": 0,
            "num_fail": 0,
            "num_uncertain": 0,
            "num_observed": 0,
            "expected_spawn": None,
            "available_reference_spawns": [],
            "easyocr_available": False,
            "easyocr_error": "stage_skipped",
        },
        "checks": [],
        "events": [],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
