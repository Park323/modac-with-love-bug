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
from .navigator.runner import AutoRunSession, parse_client_waypoints
from .playback.player import ActionPlayer


class ThreadResult:
    def __init__(self) -> None:
        self.error: BaseException | None = None


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _new_session(session_id: str) -> tuple[Path, str, dict[str, Path]]:
    started_at  = utc_now_iso()
    session_dir = create_session_dir(session_id, OUTPUT_ROOT, started_at)
    paths       = session_paths(session_dir)
    paths["input_dir"].mkdir(parents=True, exist_ok=True)
    paths["screenshots_dir"].mkdir(parents=True, exist_ok=True)
    write_manifest(session_dir, {
        "schema_version":  "1.0",
        "session_id":      session_id,
        "status":          "started",
        "test_started_at": started_at,
        "updated_at":      utc_now_iso(),
        "paths":           stringify_paths(paths),
    })
    return session_dir, started_at, paths


def _write_final_manifest(
    session_id:      str,
    session_dir:     Path,
    started_at:      str,
    status:          str,
    input_result:    dict[str, Any] | None = None,
    screen_result:   dict[str, Any] | None = None,
    auto_run_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = session_paths(session_dir)
    data: dict[str, Any] = {
        "schema_version":  "1.0",
        "session_id":      session_id,
        "status":          status,
        "test_started_at": started_at,
        "updated_at":      utc_now_iso(),
        "paths":           stringify_paths(paths),
    }
    if input_result:
        data["input"] = {"path": input_result.get("path"), "event_count": input_result.get("event_count"), "duration_sec": input_result.get("duration_sec")}
    if screen_result:
        s = screen_result.get("summary") or screen_result
        data["screen"] = {k: s.get(k) for k in ("screenshot_fps","video_fps","duration_sec","screenshot_count","video_frame_count")}
    if auto_run_result:
        data["auto_run"] = auto_run_result
    return write_manifest(session_dir, data)


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


def _start_input(session_id: str, input_path: Path, backend: str, sample_hz: float):
    recorder = create_input_recorder(backend, sample_hz=sample_hz)
    thread, result = _start_thread(recorder.start)
    _wait_started("input logger", thread, result, lambda: recorder.is_recording)
    print(f"[input] started: backend={backend}, save_path={input_path}")
    return recorder, thread, result


def _stop_input(session_id: str, input_path: Path, recorder: InputRecorder, thread, result) -> dict[str, Any]:
    recorder.stop()
    thread.join(timeout=2.0)
    if result.error:
        raise RuntimeError(f"input logger failed: {result.error}") from result.error
    saved = recorder.save(input_path, session_id)
    return {"status": "saved", "session_id": session_id, "path": str(input_path),
            "event_count": saved["session"]["event_count"], "duration_sec": saved["session"]["duration_sec"]}


def _start_screen(session_id: str, session_dir: Path, started_at: str, ss_fps: float, v_fps: float, callback_url: str | None):
    from .screen.recorder import ScreenRecorder
    recorder  = ScreenRecorder(output_root=OUTPUT_ROOT, screenshot_fps=ss_fps, video_fps=v_fps, screenshot_callback_url=callback_url)
    locations = recorder.prepare(session_id, session_dir=session_dir, test_started_at=started_at)
    thread, result = _start_thread(lambda: recorder.start(session_id))
    _wait_started("screen recorder", thread, result, lambda: recorder.is_recording)
    print(f"[screen] started: screenshot_fps={ss_fps}, video_fps={v_fps}, video_path={locations.get('video_path')}")
    return recorder, thread, result, locations


def _stop_screen(recorder, thread, result) -> dict[str, Any]:
    recorder.stop()
    thread.join(timeout=5.0)
    if result.error:
        raise RuntimeError(f"screen recorder failed: {result.error}") from result.error
    return {"status": "saved", "summary": recorder.stop()}


def _effective_screen_fps(args: argparse.Namespace) -> tuple[float, float]:
    fallback = args.fps if args.fps is not None else 30.0
    ss_fps   = args.screenshot_fps if args.screenshot_fps is not None else fallback
    v_fps    = args.video_fps      if args.video_fps      is not None else fallback
    if ss_fps <= 0:
        raise ValueError("--screenshot-fps must be > 0")
    if v_fps <= 0:
        raise ValueError("--video-fps must be > 0")
    return ss_fps, v_fps


# ── commands ──────────────────────────────────────────────────────────────────

def run_test_session(args: argparse.Namespace) -> None:
    session_dir, started_at, paths = _new_session(args.session_id)
    ss_fps, v_fps = _effective_screen_fps(args)
    input_recorder = input_thread = input_result = None
    screen_recorder = screen_thread = screen_result = None
    try:
        input_recorder, input_thread, input_result = _start_input(args.session_id, paths["input_path"], args.backend, args.sample_hz)
        screen_recorder, screen_thread, screen_result, _ = _start_screen(args.session_id, session_dir, started_at, ss_fps, v_fps, args.screenshot_callback_url)
        print(f"[test] running for {args.duration_sec}s")
        time.sleep(args.duration_sec)
    finally:
        output: dict[str, Any] = {"status": "stopped", "session_id": args.session_id}
        if input_recorder and input_thread and input_result:
            output["input"] = _stop_input(args.session_id, paths["input_path"], input_recorder, input_thread, input_result)
        if screen_recorder and screen_thread and screen_result:
            output["screen"] = _stop_screen(screen_recorder, screen_thread, screen_result)
        output["manifest"] = _write_final_manifest(args.session_id, session_dir, started_at, "stopped",
                                                    input_result=output.get("input"), screen_result=output.get("screen"))
        _print_json(output)


def run_input_record(args: argparse.Namespace) -> None:
    session_dir, started_at, paths = _new_session(args.session_id)
    recorder, thread, result = _start_input(args.session_id, paths["input_path"], args.backend, args.sample_hz)
    print(f"[input] running for {args.duration_sec}s")
    time.sleep(args.duration_sec)
    input_result = _stop_input(args.session_id, paths["input_path"], recorder, thread, result)
    manifest = _write_final_manifest(args.session_id, session_dir, started_at, "input_saved", input_result=input_result)
    _print_json({**input_result, "manifest": manifest})


def run_screen_record(args: argparse.Namespace) -> None:
    session_dir, started_at, _ = _new_session(args.session_id)
    ss_fps, v_fps = _effective_screen_fps(args)
    recorder, thread, result, _ = _start_screen(args.session_id, session_dir, started_at, ss_fps, v_fps, args.screenshot_callback_url)
    print(f"[screen] running for {args.duration_sec}s")
    time.sleep(args.duration_sec)
    screen_result = _stop_screen(recorder, thread, result)
    manifest = _write_final_manifest(args.session_id, session_dir, started_at, "screen_saved", screen_result=screen_result)
    _print_json({**screen_result, "manifest": manifest})


def run_player_sample(args: argparse.Namespace) -> None:
    print(f"[player] sending sample key press in {args.delay_sec}s")
    time.sleep(args.delay_sec)
    played = ActionPlayer().play_actions([{"t": 0.0, "type": "key_press", "key": args.key, "duration_ms": args.duration_ms}])
    _print_json({"status": "played", "action_count": played})


def run_player_file(args: argparse.Namespace) -> None:
    played = ActionPlayer().play_file(args.path)
    _print_json({"status": "played", "path": str(args.path), "action_count": played})


def run_auto_run(args: argparse.Namespace) -> None:
    """
    Load waypoints JSON and run auto-navigation.

    Waypoints file format:
      [{"idx": 0, "x": 227.4, "y": 217.3, "rot": 40.0}, ...]
    """
    wp_path = Path(args.waypoints_file)
    if not wp_path.exists():
        print(f"[ERROR] Waypoints file not found: {wp_path}")
        return

    with wp_path.open(encoding="utf-8") as f:
        raw = json.load(f)

    waypoints = parse_client_waypoints(raw)
    print(f"[auto-run] Loaded {len(waypoints)} waypoint(s) from {wp_path}")

    session_dir, started_at, paths = _new_session(args.session_id)
    nav_output = str(paths["input_path"])

    # optional screen recording
    screen_recorder = screen_thread = screen_result_obj = None
    if args.screenshot_fps or args.video_fps:
        ss_fps = args.screenshot_fps or args.video_fps or 30.0
        v_fps  = args.video_fps      or args.screenshot_fps or 30.0
        try:
            screen_recorder, screen_thread, screen_result_obj, _ = _start_screen(
                args.session_id, session_dir, started_at, ss_fps, v_fps, None
            )
        except Exception as e:
            print(f"[auto-run] Screen recorder failed to start: {e}")

    session = AutoRunSession()
    session.start(waypoints=waypoints, output_path=nav_output, session_id=args.session_id, team=args.team)
    print(f"[auto-run] Running as {args.team} — press Ctrl+C to stop early")

    try:
        while session.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[auto-run] Interrupted — stopping")
        session.stop()

    screen_result = None
    if screen_recorder and screen_thread and screen_result_obj:
        screen_result = _stop_screen(screen_recorder, screen_thread, screen_result_obj)

    summary  = session.summary()
    manifest = _write_final_manifest(
        args.session_id, session_dir, started_at, "auto_run_done",
        screen_result=screen_result, auto_run_result=summary,
    )
    _print_json({"status": summary["status"], "session_id": args.session_id,
                 "auto_run": summary, "screen": screen_result, "manifest": manifest})


# ── CLI ───────────────────────────────────────────────────────────────────────

def _add_common_record_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--session-id",   default="local_test_001")
    p.add_argument("--duration-sec", type=float, default=5.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run test_auto_run_executor flows locally.")
    sub    = parser.add_subparsers(dest="command", required=True)

    # test-session
    ts = sub.add_parser("test-session", help="input + screen recording, then stop")
    _add_common_record_args(ts)
    ts.add_argument("--backend", choices=["hook", "polling"], default="polling")
    ts.add_argument("--sample-hz", type=float, default=120.0)
    ts.add_argument("--screenshot-fps", type=float)
    ts.add_argument("--video-fps", type=float)
    ts.add_argument("--fps", type=float)
    ts.add_argument("--screenshot-callback-url")
    ts.set_defaults(func=run_test_session)

    # input-record
    ir = sub.add_parser("input-record", help="input recording only")
    _add_common_record_args(ir)
    ir.add_argument("--backend", choices=["hook", "polling"], default="polling")
    ir.add_argument("--sample-hz", type=float, default=120.0)
    ir.set_defaults(func=run_input_record)

    # screen-record
    sr = sub.add_parser("screen-record", help="screen recording only")
    _add_common_record_args(sr)
    sr.add_argument("--screenshot-fps", type=float)
    sr.add_argument("--video-fps", type=float)
    sr.add_argument("--fps", type=float)
    sr.add_argument("--screenshot-callback-url")
    sr.set_defaults(func=run_screen_record)

    # player-sample
    ps = sub.add_parser("player-sample", help="send a single key press")
    ps.add_argument("--key",         default="W")
    ps.add_argument("--duration-ms", type=float, default=50.0)
    ps.add_argument("--delay-sec",   type=float, default=3.0)
    ps.set_defaults(func=run_player_sample)

    # player-file
    pf = sub.add_parser("player-file", help="replay a JSON action file")
    pf.add_argument("path", type=Path)
    pf.set_defaults(func=run_player_file)

    # auto-run  ← new
    ar = sub.add_parser("auto-run", help="run auto-navigation from client waypoints JSON")
    ar.add_argument("waypoints_file",  help="path to waypoints JSON file ([{idx,x,y,rot}])")
    ar.add_argument("--session-id",    default="auto_run_001")
    ar.add_argument("--team",          choices=["BL", "GR"], default="BL")
    ar.add_argument("--screenshot-fps", type=float, default=None)
    ar.add_argument("--video-fps",      type=float, default=None)
    ar.set_defaults(func=run_auto_run)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
