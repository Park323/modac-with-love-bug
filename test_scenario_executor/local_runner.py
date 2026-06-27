"""Run API-equivalent flows locally without starting the HTTP server."""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .input_logger import InputRecorder, create_input_recorder
from .player import ActionPlayer

OUTPUT_ROOT = Path("test_scenario_executor_output")
INPUT_RECORDINGS_DIR = OUTPUT_ROOT / "input_recordings"
SCREEN_RECORDINGS_DIR = OUTPUT_ROOT / "screen_recordings"


class ThreadResult:
    def __init__(self) -> None:
        self.error: BaseException | None = None


def _input_path(session_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
    return INPUT_RECORDINGS_DIR / f"{safe_id}.json"


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


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
    backend: str,
    sample_hz: float,
) -> tuple[InputRecorder, threading.Thread, ThreadResult]:
    recorder = create_input_recorder(backend, sample_hz=sample_hz)
    thread, result = _start_thread(recorder.start)
    _wait_started("input logger", thread, result, lambda: recorder.is_recording)
    print(f"[input] started: backend={backend}, save_path={_input_path(session_id)}")
    return recorder, thread, result


def _stop_input(
    session_id: str,
    recorder: InputRecorder,
    thread: threading.Thread,
    result: ThreadResult,
) -> dict[str, Any]:
    recorder.stop()
    thread.join(timeout=2.0)
    if result.error:
        raise RuntimeError(f"input logger failed: {result.error}") from result.error
    path = _input_path(session_id)
    saved = recorder.save(path, session_id)
    return {
        "status": "saved",
        "session_id": session_id,
        "path": str(path),
        "event_count": saved["session"]["event_count"],
        "duration_sec": saved["session"]["duration_sec"],
    }


def _start_screen(
    session_id: str,
    fps: float,
    screenshot_callback_url: str | None,
) -> tuple[Any, threading.Thread, ThreadResult, dict[str, str | None]]:
    from .screen_recorder import ScreenRecorder

    recorder = ScreenRecorder(
        output_root=SCREEN_RECORDINGS_DIR,
        fps=fps,
        screenshot_callback_url=screenshot_callback_url,
    )
    locations = recorder.prepare(session_id)
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
    return {"status": "saved", "manifest": recorder.stop()}


def run_test_session(args: argparse.Namespace) -> None:
    """Equivalent to /test/start, waiting, then /test/stop."""
    input_recorder: InputRecorder | None = None
    input_thread: threading.Thread | None = None
    input_result: ThreadResult | None = None
    screen_recorder: Any | None = None
    screen_thread: threading.Thread | None = None
    screen_result: ThreadResult | None = None

    try:
        input_recorder, input_thread, input_result = _start_input(
            args.session_id, args.backend, args.sample_hz
        )
        screen_recorder, screen_thread, screen_result, locations = _start_screen(
            args.session_id, args.fps, args.screenshot_callback_url
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
                args.session_id, input_recorder, input_thread, input_result
            )
        if screen_recorder and screen_thread and screen_result:
            output["screen"] = _stop_screen(screen_recorder, screen_thread, screen_result)
        _print_json(output)


def run_input_record(args: argparse.Namespace) -> None:
    recorder, thread, result = _start_input(args.session_id, args.backend, args.sample_hz)
    print(f"[input] running for {args.duration_sec}s")
    time.sleep(args.duration_sec)
    _print_json(_stop_input(args.session_id, recorder, thread, result))


def run_screen_record(args: argparse.Namespace) -> None:
    recorder, thread, result, _locations = _start_screen(
        args.session_id, args.fps, args.screenshot_callback_url
    )
    print(f"[screen] running for {args.duration_sec}s")
    time.sleep(args.duration_sec)
    _print_json(_stop_screen(recorder, thread, result))


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
