"""Run API-equivalent flows locally without starting the HTTP server."""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .core.session_paths import (
    OUTPUT_ROOT,
    create_session_dir,
    session_paths,
    stringify_paths,
    utc_now_iso,
    write_manifest,
)
from .input.logger import InputRecorder, create_input_recorder
from .playback.player import ActionPlayer


class ThreadResult:
    def __init__(self) -> None:
        self.error: BaseException | None = None


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _new_session(session_id: str) -> tuple[Path, str, dict[str, Path]]:
    started_at = utc_now_iso()
    session_dir = create_session_dir(session_id, OUTPUT_ROOT, started_at)
    paths = session_paths(session_dir)
    paths["input_dir"].mkdir(parents=True, exist_ok=True)
    paths["screenshots_dir"].mkdir(parents=True, exist_ok=True)
    write_manifest(
        session_dir,
        {
            "schema_version": "1.0",
            "session_id": session_id,
            "status": "started",
            "test_started_at": started_at,
            "updated_at": utc_now_iso(),
            "paths": stringify_paths(paths),
        },
    )
    return session_dir, started_at, paths


def _write_final_manifest(
    session_id: str,
    session_dir: Path,
    started_at: str,
    status: str,
    input_result: dict[str, Any] | None = None,
    screen_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = session_paths(session_dir)
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "session_id": session_id,
        "status": status,
        "test_started_at": started_at,
        "updated_at": utc_now_iso(),
        "paths": stringify_paths(paths),
    }
    if input_result:
        data["input"] = _manifest_input_summary(input_result)
    if screen_result:
        data["screen"] = _manifest_screen_summary(screen_result)
    return write_manifest(session_dir, data)


def _manifest_input_summary(input_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": input_result.get("path"),
        "event_count": input_result.get("event_count"),
        "duration_sec": input_result.get("duration_sec"),
    }


def _manifest_screen_summary(screen_result: dict[str, Any]) -> dict[str, Any]:
    summary = screen_result.get("summary")
    if isinstance(summary, dict):
        return {
            "fps": summary.get("fps"),
            "started_at": summary.get("started_at"),
            "stopped_at": summary.get("stopped_at"),
            "duration_sec": summary.get("duration_sec"),
            "frame_count": summary.get("frame_count"),
            "screenshot_callback_url": summary.get("screenshot_callback_url"),
        }
    return {
        "fps": screen_result.get("fps"),
        "started_at": screen_result.get("started_at"),
        "stopped_at": screen_result.get("stopped_at"),
        "duration_sec": screen_result.get("duration_sec"),
        "frame_count": screen_result.get("frame_count"),
        "screenshot_callback_url": screen_result.get("screenshot_callback_url"),
    }


def _start_thread(target: Callable[[], None]) -> tuple[threading.Thread, ThreadResult]:
    result = ThreadResult()

    def run() -> None:
        try:
            target()
        except BaseException as exc:
            result.error = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def _wait_started(
    label: str,
    thread: threading.Thread,
    result: ThreadResult,
    is_running: Callable[[], bool],
    timeout_sec: float = 2.0,
) -> None:
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        if result.error:
            raise RuntimeError(f"{label} failed to start: {result.error}") from result.error
        if is_running():
            return
        if not thread.is_alive():
            raise RuntimeError(f"{label} stopped before startup completed")
        time.sleep(0.02)
    raise RuntimeError(f"{label} did not report running within {timeout_sec}s")


def _start_input(
    session_id: str,
    input_path: Path,
    backend: str,
    sample_hz: float,
) -> tuple[InputRecorder, threading.Thread, ThreadResult]:
    recorder = create_input_recorder(backend, sample_hz=sample_hz)
    thread, result = _start_thread(recorder.start)
    _wait_started("input logger", thread, result, lambda: recorder.is_recording)
    print(f"[input] started: backend={backend}, save_path={input_path}")
    return recorder, thread, result


def _stop_input(
    session_id: str,
    input_path: Path,
    recorder: InputRecorder,
    thread: threading.Thread,
    result: ThreadResult,
) -> dict[str, Any]:
    recorder.stop()
    thread.join(timeout=2.0)
    if result.error:
        raise RuntimeError(f"input logger failed: {result.error}") from result.error
    saved = recorder.save(input_path, session_id)
    return {
        "status": "saved",
        "session_id": session_id,
        "path": str(input_path),
        "event_count": saved["session"]["event_count"],
        "duration_sec": saved["session"]["duration_sec"],
    }


def _start_screen(
    session_id: str,
    session_dir: Path,
    started_at: str,
    fps: float,
    screenshot_callback_url: str | None,
) -> tuple[Any, threading.Thread, ThreadResult, dict[str, str | None]]:
    from .screen.recorder import ScreenRecorder

    recorder = ScreenRecorder(
        output_root=OUTPUT_ROOT,
        fps=fps,
        screenshot_callback_url=screenshot_callback_url,
    )
    locations = recorder.prepare(session_id, session_dir=session_dir, test_started_at=started_at)
    thread, result = _start_thread(lambda: recorder.start(session_id))
    _wait_started("screen recorder", thread, result, lambda: recorder.is_recording)
    print(f"[screen] started: fps={fps}, video_path={locations.get('video_path')}")
    return recorder, thread, result, locations


def _stop_screen(
    recorder: Any,
    thread: threading.Thread,
    result: ThreadResult,
) -> dict[str, Any]:
    recorder.stop()
    thread.join(timeout=5.0)
    if result.error:
        raise RuntimeError(f"screen recorder failed: {result.error}") from result.error
    return {"status": "saved", "summary": recorder.stop()}


def run_test_session(args: argparse.Namespace) -> None:
    """Equivalent to /test/start, waiting, then /test/stop."""
    input_recorder: InputRecorder | None = None
    input_thread: threading.Thread | None = None
    input_result: ThreadResult | None = None
    screen_recorder: Any | None = None
    screen_thread: threading.Thread | None = None
    screen_result: ThreadResult | None = None

    session_dir, started_at, paths = _new_session(args.session_id)
    try:
        input_recorder, input_thread, input_result = _start_input(
            args.session_id, paths["input_path"], args.backend, args.sample_hz
        )
        screen_recorder, screen_thread, screen_result, locations = _start_screen(
            args.session_id, session_dir, started_at, args.fps, args.screenshot_callback_url
        )
        print(f"[test] running for {args.duration_sec}s")
        time.sleep(args.duration_sec)
    finally:
        output: dict[str, Any] = {
            "status": "stopped",
            "session_id": args.session_id,
        }
        if input_recorder and input_thread and input_result:
            output["input"] = _stop_input(
                args.session_id, paths["input_path"], input_recorder, input_thread, input_result
            )
        if screen_recorder and screen_thread and screen_result:
            output["screen"] = _stop_screen(screen_recorder, screen_thread, screen_result)
        output["manifest"] = _write_final_manifest(
            args.session_id,
            session_dir,
            started_at,
            "stopped",
            input_result=output.get("input"),
            screen_result=output.get("screen"),
        )
        _print_json(output)


def run_input_record(args: argparse.Namespace) -> None:
    session_dir, started_at, paths = _new_session(args.session_id)
    recorder, thread, result = _start_input(
        args.session_id, paths["input_path"], args.backend, args.sample_hz
    )
    print(f"[input] running for {args.duration_sec}s")
    time.sleep(args.duration_sec)
    input_result = _stop_input(args.session_id, paths["input_path"], recorder, thread, result)
    manifest = _write_final_manifest(
        args.session_id, session_dir, started_at, "input_saved", input_result=input_result
    )
    _print_json({**input_result, "manifest": manifest})


def run_screen_record(args: argparse.Namespace) -> None:
    session_dir, started_at, _paths = _new_session(args.session_id)
    recorder, thread, result, _locations = _start_screen(
        args.session_id, session_dir, started_at, args.fps, args.screenshot_callback_url
    )
    print(f"[screen] running for {args.duration_sec}s")
    time.sleep(args.duration_sec)
    screen_result = _stop_screen(recorder, thread, result)
    manifest = _write_final_manifest(
        args.session_id, session_dir, started_at, "screen_saved", screen_result=screen_result
    )
    _print_json({**screen_result, "manifest": manifest})


def run_player_sample(args: argparse.Namespace) -> None:
    print(f"[player] sending sample key press in {args.delay_sec}s")
    time.sleep(args.delay_sec)
    played = ActionPlayer().play_actions([
        {
            "t": 0.0,
            "type": "key_press",
            "key": args.key,
            "duration_ms": args.duration_ms,
        }
    ])
    _print_json({"status": "played", "action_count": played})


def run_player_file(args: argparse.Namespace) -> None:
    played = ActionPlayer().play_file(args.path)
    _print_json({"status": "played", "path": str(args.path), "action_count": played})


def _add_common_record_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id", default="local_test_001")
    parser.add_argument("--duration-sec", type=float, default=5.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run test_scenario_executor flows locally without HTTP."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    test_session = subparsers.add_parser(
        "test-session",
        help="run /test/start flow, wait, then run /test/stop flow",
    )
    _add_common_record_args(test_session)
    test_session.add_argument("--backend", choices=["hook", "polling"], default="polling")
    test_session.add_argument("--sample-hz", type=float, default=120.0)
    test_session.add_argument("--fps", type=float, default=30.0)
    test_session.add_argument("--screenshot-callback-url")
    test_session.set_defaults(func=run_test_session)

    input_record = subparsers.add_parser(
        "input-record",
        help="run /input/record/start flow, wait, then run /input/record/stop flow",
    )
    _add_common_record_args(input_record)
    input_record.add_argument("--backend", choices=["hook", "polling"], default="polling")
    input_record.add_argument("--sample-hz", type=float, default=120.0)
    input_record.set_defaults(func=run_input_record)

    screen_record = subparsers.add_parser(
        "screen-record",
        help="run /screen/record/start flow, wait, then run /screen/record/stop flow",
    )
    _add_common_record_args(screen_record)
    screen_record.add_argument("--fps", type=float, default=30.0)
    screen_record.add_argument("--screenshot-callback-url")
    screen_record.set_defaults(func=run_screen_record)

    player_sample = subparsers.add_parser(
        "player-sample",
        help="run /player/play flow with a short key press",
    )
    player_sample.add_argument("--key", default="W")
    player_sample.add_argument("--duration-ms", type=float, default=50.0)
    player_sample.add_argument("--delay-sec", type=float, default=3.0)
    player_sample.set_defaults(func=run_player_sample)

    player_file = subparsers.add_parser(
        "player-file",
        help="run /player/play-file flow with a JSON action file",
    )
    player_file.add_argument("path", type=Path)
    player_file.set_defaults(func=run_player_file)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
