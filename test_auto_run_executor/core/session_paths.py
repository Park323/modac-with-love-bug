"""Session output path helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTPUT_ROOT = Path("test_auto_run_executor_output")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_session_id(session_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)


def create_session_dir(
    session_id: str,
    output_root: str | Path = OUTPUT_ROOT,
    started_at: str | None = None,
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = safe_session_id(session_id)
    return Path(output_root) / f"{safe_id}_{timestamp}"


def session_paths(session_dir: str | Path) -> dict[str, Path]:
    base = Path(session_dir)
    input_dir = base / "input_recording"
    screen_dir = base / "screen_recording"
    return {
        "session_dir": base,
        "input_dir": input_dir,
        "input_path": input_dir / "input.json",
        "screen_dir": screen_dir,
        "screenshots_dir": screen_dir / "screenshots",
        "video_path": screen_dir / "screen.mp4",
        "manifest_path": base / "manifest.json",
    }


def stringify_paths(paths: dict[str, Path]) -> dict[str, str]:
    return {key: str(value) for key, value in paths.items()}


def write_manifest(session_dir: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    paths = session_paths(session_dir)
    paths["manifest_path"].parent.mkdir(parents=True, exist_ok=True)
    with paths["manifest_path"].open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data
