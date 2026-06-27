from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = ROOT_DIR / "configs"


def _default_config(name: str) -> str | None:
    path = CONFIG_DIR / name
    return str(path) if path.exists() else None


def _json_dump(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_command(cmd: list[str], *, cwd: Path, dry_run: bool = False) -> dict[str, Any]:
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


def build_easyocr_command(args: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, str(ROOT_DIR / "detector_layer" / "setup_optional_deps.py"), "--easyocr"]
    if args.user_install:
        cmd.append("--user")
    if args.upgrade_install:
        cmd.append("--upgrade")
    return cmd


def build_bootstrap_command(args: argparse.Namespace, bootstrap_out: Path) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT_DIR / "bootstrap.py"),
        "--dataset",
        args.dataset,
        "--out",
        str(bootstrap_out),
        "--all",
        "--sample-fps",
        str(args.bootstrap_sample_fps),
        "--resize",
        args.resize,
        "--base-resolution",
        args.base_resolution,
        "--roi-config",
        args.roi_config,
        "--score-config",
        args.score_config,
        "--notification-config",
        args.notification_config,
        "--digit-label-source",
        args.digit_label_source,
        "--notification-copy-mode",
        args.notification_copy_mode,
    ]
    if args.save_unlabeled_digits:
        cmd.append("--save-unlabeled-digits")
    if args.install_easyocr:
        cmd.append("--install-easyocr")
    if args.ui_templates:
        cmd.extend(["--ui-templates", args.ui_templates])
    if args.max_frames is not None:
        cmd.extend(["--max-frames", str(args.max_frames)])
    if args.include_non_gameplay:
        cmd.append("--include-non-gameplay")
    if args.no_normalize:
        cmd.append("--no-normalize")
    if args.no_anchor_correction:
        cmd.append("--no-anchor-correction")
    if args.keep_going:
        cmd.append("--keep-going")
    return cmd


def build_temporal_command(args: argparse.Namespace, temporal_out: Path) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT_DIR / "bootstrap_temporal_notifications.py"),
        "--dataset",
        args.dataset,
        "--out",
        str(temporal_out),
        "--sample-fps",
        str(args.temporal_sample_fps),
        "--resize",
        args.resize,
        "--base-resolution",
        args.base_resolution,
        "--roi-config",
        args.roi_config,
        "--score-config",
        args.score_config,
        "--score-change-hash-threshold",
        str(args.score_change_hash_threshold),
        "--min-event-gap-sec",
        str(args.min_event_gap_sec),
        "--dedupe-threshold",
        str(args.dedupe_threshold),
        "--easyocr-model-dir",
        args.easyocr_model_dir,
        "--ocr-min-confidence",
        str(args.ocr_min_confidence),
        "--ocr-scale",
        str(args.ocr_scale),
    ]
    if args.local_player_name:
        cmd.extend(["--local-player-name", args.local_player_name])
    if args.local_team:
        cmd.extend(["--local-team", args.local_team])
    if args.install_easyocr:
        cmd.append("--install-easyocr")
    if args.require_easyocr:
        cmd.append("--require-easyocr")
    if args.user_install:
        cmd.append("--user-install")
    if args.upgrade_install:
        cmd.append("--upgrade-install")
    if args.disable_easyocr:
        cmd.append("--disable-easyocr")
    if args.disable_ocr_preprocess:
        cmd.append("--disable-ocr-preprocess")
    if args.include_first_kill_medal:
        cmd.append("--include-first-kill-medal")
    if args.ui_templates:
        cmd.extend(["--ui-templates", args.ui_templates])
    if args.max_frames is not None:
        cmd.extend(["--max-frames", str(args.max_frames)])
    if args.include_non_gameplay:
        cmd.append("--include-non-gameplay")
    if args.no_normalize:
        cmd.append("--no-normalize")
    if args.no_anchor_correction:
        cmd.append("--no-anchor-correction")
    return cmd


def build_auto_promote_command(args: argparse.Namespace, bootstrap_out: Path, temporal_out: Path, promotion_out: Path) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT_DIR / "auto_promote_assets.py"),
        "--temporal-assets",
        str(temporal_out),
        "--bootstrap-assets",
        str(bootstrap_out),
        "--out",
        str(promotion_out),
        "--kill-feed-promote-threshold",
        str(args.kill_feed_promote_threshold),
        "--death-panel-promote-threshold",
        str(args.death_panel_promote_threshold),
        "--first-kill-promote-threshold",
        str(args.first_kill_promote_threshold),
        "--dedupe-threshold",
        str(args.dedupe_threshold),
    ]
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run non-VLM asset preparation steps: EasyOCR setup, conservative bootstrap, temporal notification mining."
    )
    parser.add_argument("--dataset", required=True, help="Dataset directory or a single video file.")
    parser.add_argument("--out", default=str(ROOT_DIR / "outputs" / "prepared_assets"))
    parser.add_argument("--resize", default="1920x1080")
    parser.add_argument("--base-resolution", default="1920x1080")
    parser.add_argument("--roi-config", default=_default_config("roi_config.example.json"))
    parser.add_argument("--score-config", default=_default_config("score_reader_config.example.json"))
    parser.add_argument("--notification-config", default=_default_config("notification_config.example.json"))
    parser.add_argument("--ui-templates", default=None)

    parser.add_argument("--bootstrap-sample-fps", type=float, default=5.0)
    parser.add_argument("--temporal-sample-fps", type=float, default=5.0)
    parser.add_argument("--max-frames", type=int, default=None)

    parser.add_argument("--digit-label-source", choices=["easyocr", "manual-json", "none"], default="none")
    parser.add_argument("--save-unlabeled-digits", action="store_true", default=True)
    parser.add_argument("--notification-copy-mode", choices=["selected", "all-candidates", "candidates-only"], default="candidates-only")

    parser.add_argument("--local-player-name", default=None)
    parser.add_argument("--local-team", choices=["GR", "BL"], default=None)
    parser.add_argument("--score-change-hash-threshold", type=int, default=20)
    parser.add_argument("--min-event-gap-sec", type=float, default=2.0)
    parser.add_argument("--dedupe-threshold", type=int, default=8)
    parser.add_argument("--include-first-kill-medal", action="store_true")
    parser.add_argument("--skip-auto-promote", action="store_true", help="Do not run conservative automatic template promotion.")
    parser.add_argument("--kill-feed-promote-threshold", type=float, default=0.78)
    parser.add_argument("--death-panel-promote-threshold", type=float, default=0.78)
    parser.add_argument("--first-kill-promote-threshold", type=float, default=0.90)

    parser.add_argument("--install-easyocr", action="store_true", default=True)
    parser.add_argument("--require-easyocr", action="store_true", default=True)
    parser.add_argument("--user-install", action="store_true")
    parser.add_argument("--upgrade-install", action="store_true")
    parser.add_argument("--easyocr-model-dir", default=str(ROOT_DIR / "outputs" / "easyocr_models"))
    parser.add_argument("--disable-easyocr", action="store_true")
    parser.add_argument("--ocr-min-confidence", type=float, default=0.35)
    parser.add_argument("--disable-ocr-preprocess", action="store_true")
    parser.add_argument("--ocr-scale", type=int, default=3)

    parser.add_argument("--include-non-gameplay", action="store_true")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out).expanduser().resolve()
    bootstrap_out = out_dir / "bootstrap_assets"
    temporal_out = out_dir / "temporal_notification_assets"
    promotion_out = out_dir / "promoted_assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    stages: list[dict[str, Any]] = []
    commands = [
        ("easyocr_setup", build_easyocr_command(args), ROOT_DIR),
        ("bootstrap_assets", build_bootstrap_command(args, bootstrap_out), ROOT_DIR),
        ("temporal_notifications", build_temporal_command(args, temporal_out), ROOT_DIR),
    ]
    if not args.skip_auto_promote:
        commands.append(("auto_promote_assets", build_auto_promote_command(args, bootstrap_out, temporal_out, promotion_out), ROOT_DIR))

    for name, cmd, cwd in commands:
        print(f"[{name}] {' '.join(cmd)}", flush=True)
        result = run_command(cmd, cwd=cwd, dry_run=args.dry_run)
        stages.append({"name": name, **result})
        if result["returncode"] != 0 and not args.keep_going:
            break

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "Non-VLM asset preparation wrapper for steps 0, 1, and 2.",
        "dataset": str(Path(args.dataset).expanduser()),
        "output_dir": str(out_dir),
        "stage_dirs": {
            "bootstrap_assets": str(bootstrap_out),
            "temporal_notification_assets": str(temporal_out),
            "promoted_assets": str(promotion_out),
        },
        "recommended_next_step": {
            "command": [
                sys.executable,
                str(ROOT_DIR / "run.py"),
                "--dataset",
                args.dataset,
                "--out",
                str(out_dir / "final_run"),
                "--digit-templates",
                str(promotion_out / "digit_templates"),
                "--notification-templates",
                str(promotion_out / "notification_templates"),
                "--install-easyocr",
            ]
        },
        "stages": stages,
        "status": "ok" if all(s["returncode"] == 0 for s in stages) and len(stages) == len(commands) else "failed",
    }
    manifest_path = out_dir / "prepare_assets_manifest.json"
    _json_dump(manifest, manifest_path)

    print(json.dumps({
        "manifest_path": str(manifest_path),
        "bootstrap_assets": str(bootstrap_out),
        "temporal_notification_assets": str(temporal_out),
        "promoted_assets": str(promotion_out),
        "status": manifest["status"],
    }, ensure_ascii=False, indent=2))

    if manifest["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
