from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
DETECTOR_DIR = ROOT_DIR / "detector_layer"
CONFIG_DIR = ROOT_DIR / "configs"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
DIGIT_DIRS = {str(i) for i in range(10)} | {"unlabeled_candidates"}
NOTIFICATION_DIRS = {"kill_feed", "first_kill_medal", "death_killer_panel", "candidates"}


def _default_config(name: str) -> str | None:
    path = CONFIG_DIR / name
    return str(path) if path.exists() else None


def _safe_name(path: Path) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in path.stem.strip())
    return safe or "video"


def _json_dump(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def discover_videos(dataset: Path) -> list[Path]:
    if dataset.is_file():
        if dataset.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(f"Unsupported video extension: {dataset}")
        return [dataset.resolve()]
    if not dataset.exists():
        raise FileNotFoundError(f"Dataset path not found: {dataset}")
    return sorted(p.resolve() for p in dataset.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def run_command(cmd: list[str], dry_run: bool = False) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    if dry_run:
        return {
            "command": cmd,
            "cwd": str(DETECTOR_DIR),
            "returncode": 0,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stdout": "",
            "stderr": "",
            "dry_run": True,
        }
    proc = subprocess.run(cmd, cwd=str(DETECTOR_DIR), text=True, capture_output=True)
    return {
        "command": cmd,
        "cwd": str(DETECTOR_DIR),
        "returncode": proc.returncode,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "dry_run": False,
    }


def copy_tree_contents(src: Path, dst: Path, *, prefix: str, allowed_top_dirs: set[str]) -> int:
    if not src.exists():
        return 0
    copied = 0
    for top in sorted(p for p in src.iterdir() if p.is_dir() and p.name in allowed_top_dirs):
        for item in sorted(p for p in top.rglob("*") if p.is_file()):
            rel = item.relative_to(top)
            target_dir = dst / top.name / rel.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{prefix}_{item.name}"
            shutil.copy2(item, target)
            copied += 1
    return copied


def build_digit_command(video: Path, out_dir: Path, args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "bootstrap_digit_templates.py",
        "--video",
        str(video),
        "--out",
        str(out_dir),
        "--score-config",
        args.score_config,
        "--label-source",
        args.digit_label_source,
        "--sample-fps",
        str(args.sample_fps),
        "--resize",
        args.resize,
        "--base-resolution",
        args.base_resolution,
        "--roi-config",
        args.roi_config,
    ]
    if args.digit_label_source == "manual-json" and args.manual_digit_labels:
        cmd.extend(["--manual-labels", args.manual_digit_labels])
    if args.install_easyocr:
        cmd.append("--install-easyocr")
    if args.save_unlabeled_digits:
        cmd.append("--save-unlabeled")
    if args.ui_templates:
        cmd.extend(["--ui-templates", args.ui_templates])
    if args.max_frames is not None:
        cmd.extend(["--max-frames", str(args.max_frames)])
    if args.no_normalize:
        cmd.append("--no-normalize")
    if args.no_anchor_correction:
        cmd.append("--no-anchor-correction")
    if args.include_non_gameplay:
        cmd.append("--include-non-gameplay")
    return cmd


def build_notification_command(video: Path, out_dir: Path, args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "bootstrap_notification_templates.py",
        "--video",
        str(video),
        "--out",
        str(out_dir),
        "--notification-config",
        args.notification_config,
        "--min-score",
        str(args.notification_min_score),
        "--top-k-per-class",
        str(args.notification_top_k),
        "--sample-fps",
        str(args.sample_fps),
        "--resize",
        args.resize,
        "--base-resolution",
        args.base_resolution,
        "--roi-config",
        args.roi_config,
    ]
    if args.notification_copy_mode:
        cmd.extend(["--copy-mode", args.notification_copy_mode])
    if args.existing_notification_templates:
        cmd.extend(["--existing-templates", args.existing_notification_templates])
    if args.ui_templates:
        cmd.extend(["--ui-templates", args.ui_templates])
    if args.max_frames is not None:
        cmd.extend(["--max-frames", str(args.max_frames)])
    if args.no_normalize:
        cmd.append("--no-normalize")
    if args.no_anchor_correction:
        cmd.append("--no-anchor-correction")
    if args.include_non_gameplay:
        cmd.append("--include-non-gameplay")
    return cmd


def run_bootstrap_for_video(video: Path, out_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    name = _safe_name(video)
    work_dir = out_root / "work" / name
    result: dict[str, Any] = {
        "video": str(video),
        "video_name": video.name,
        "work_dir": str(work_dir),
        "stages": [],
        "status": "COMPLETED",
    }

    if args.digits:
        digit_tmp = work_dir / "digit_templates"
        cmd = build_digit_command(video, digit_tmp, args)
        print(f"[{video.name}] digit template bootstrap ...", flush=True)
        stage = run_command(cmd, dry_run=args.dry_run)
        copied = 0
        if stage["returncode"] == 0 and not args.dry_run:
            copied = copy_tree_contents(
                digit_tmp,
                out_root / "digit_templates",
                prefix=name,
                allowed_top_dirs=DIGIT_DIRS,
            )
        stage["copied_assets"] = copied
        result["stages"].append({"name": "digit_templates", **stage})
        if stage["returncode"] != 0:
            result["status"] = "FAILED"
            if not args.keep_going:
                raise RuntimeError(f"Digit bootstrap failed for {video}")

    if args.notifications:
        notification_tmp = work_dir / "notification_templates"
        cmd = build_notification_command(video, notification_tmp, args)
        print(f"[{video.name}] notification template bootstrap ...", flush=True)
        stage = run_command(cmd, dry_run=args.dry_run)
        copied = 0
        if stage["returncode"] == 0 and not args.dry_run:
            copied = copy_tree_contents(
                notification_tmp,
                out_root / "notification_templates",
                prefix=name,
                allowed_top_dirs=NOTIFICATION_DIRS,
            )
        stage["copied_assets"] = copied
        result["stages"].append({"name": "notification_templates", **stage})
        if stage["returncode"] != 0:
            result["status"] = "FAILED"
            if not args.keep_going:
                raise RuntimeError(f"Notification bootstrap failed for {video}")

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap reusable QA assets from a CrossFire video dataset.")
    parser.add_argument("--dataset", required=True, help="Directory containing videos, or a single video file.")
    parser.add_argument("--out", default=str(ROOT_DIR / "outputs" / "bootstrap_assets"), help="Bootstrap asset output root.")
    parser.add_argument("--digits", action="store_true", help="Bootstrap score digit templates/candidates.")
    parser.add_argument("--notifications", action="store_true", help="Bootstrap notification templates/candidates.")
    parser.add_argument("--all", action="store_true", help="Run all pre-run bootstraps.")

    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--resize", default="1920x1080")
    parser.add_argument("--base-resolution", default="1920x1080")
    parser.add_argument("--max-frames", type=int, default=None)

    parser.add_argument("--roi-config", default=_default_config("roi_config.example.json"))
    parser.add_argument("--score-config", default=_default_config("score_reader_config.example.json"))
    parser.add_argument("--notification-config", default=_default_config("notification_config.example.json"))
    parser.add_argument("--ui-templates", default=None)

    parser.add_argument("--digit-label-source", choices=["easyocr", "manual-json", "none"], default="none")
    parser.add_argument("--manual-digit-labels", default=None)
    parser.add_argument("--save-unlabeled-digits", action="store_true")
    parser.add_argument("--install-easyocr", action="store_true")
    parser.add_argument("--notification-min-score", type=float, default=0.55)
    parser.add_argument("--notification-top-k", type=int, default=40)
    parser.add_argument(
        "--notification-copy-mode",
        choices=["selected", "all-candidates", "candidates-only"],
        default="candidates-only",
        help="Use candidates-only by default so heuristic false positives are not promoted to templates.",
    )
    parser.add_argument("--existing-notification-templates", default=None, help="Seed templates used to score notification candidates.")

    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--no-anchor-correction", action="store_true")
    parser.add_argument("--include-non-gameplay", action="store_true", help="include menu/loading frames during bootstrap")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.all:
        args.digits = True
        args.notifications = True
    if not args.digits and not args.notifications:
        args.digits = True
        args.notifications = True
    if args.digit_label_source == "manual-json" and not args.manual_digit_labels:
        raise SystemExit("--manual-digit-labels is required when --digit-label-source manual-json")

    dataset = Path(args.dataset).expanduser()
    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    videos = discover_videos(dataset)
    if not videos:
        raise SystemExit(f"No videos found under: {dataset}")

    print(f"Found {len(videos)} video(s). Bootstrap output: {out_root}", flush=True)
    results = [run_bootstrap_for_video(video, out_root, args) for video in videos]
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset.resolve()),
        "output_dir": str(out_root),
        "asset_dirs": {
            "digit_templates": str(out_root / "digit_templates"),
            "notification_templates": str(out_root / "notification_templates"),
        },
        "options": {
            "digits": args.digits,
            "notifications": args.notifications,
            "digit_label_source": args.digit_label_source,
            "notification_min_score": args.notification_min_score,
            "notification_top_k": args.notification_top_k,
            "sample_fps": args.sample_fps,
            "resize": args.resize,
        },
        "videos": results,
    }
    manifest_path = out_root / "bootstrap_manifest.json"
    _json_dump(manifest, manifest_path)
    print(json.dumps({
        "manifest_path": str(manifest_path),
        "digit_templates": str(out_root / "digit_templates"),
        "notification_templates": str(out_root / "notification_templates"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
