"""FastAPI surface for input recording, screen recording, and JSON playback."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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
from .screen.recorder import ScreenRecorder

app = FastAPI(title="Test Scenario Executor API", version="1.0.0")

_input_recorder: InputRecorder | None = None
_input_thread: threading.Thread | None = None
_input_start_error: BaseException | None = None
_input_lock = threading.Lock()

_screen_recorder = ScreenRecorder(output_root=OUTPUT_ROOT, fps=30.0)
_screen_thread: threading.Thread | None = None
_screen_lock = threading.Lock()

_player = ActionPlayer(jitter_ms=0.0)
_player_thread: threading.Thread | None = None
_player_lock = threading.Lock()

_active_session_id: str | None = None
_active_session_dir: Path | None = None
_active_test_started_at: str | None = None


class InputRecordStart(BaseModel):
    session_id: str = "session"
    backend: Literal["hook", "polling"] = "hook"
    sample_hz: float = 120.0


class SessionRequest(BaseModel):
    session_id: str = "session"


class ScreenStartRequest(BaseModel):
    session_id: str = "session"
    fps: float = 30.0
    screenshot_callback_url: str | None = None


class TestStartRequest(BaseModel):
    session_id: str = "session"
    backend: Literal["hook", "polling"] = "hook"
    sample_hz: float = 120.0
    fps: float = 30.0
    screenshot_callback_url: str | None = None


class PlayFileRequest(BaseModel):
    path: str


def _input_path(session_id: str) -> Path:
    session_dir, _started_at = _ensure_session(session_id)
    return session_paths(session_dir)["input_path"]


def _ensure_session(session_id: str) -> tuple[Path, str]:
    global _active_session_id, _active_session_dir, _active_test_started_at
    if _active_session_dir and _active_session_id == session_id and _active_test_started_at:
        return _active_session_dir, _active_test_started_at

    if (
        _active_session_dir
        and _active_session_id != session_id
        and ((_input_recorder and _input_recorder.is_recording) or _screen_recorder.is_recording)
    ):
        raise HTTPException(400, f"Another session is already active: {_active_session_id}")

    _active_session_id = session_id
    _active_test_started_at = utc_now_iso()
    _active_session_dir = create_session_dir(session_id, OUTPUT_ROOT, _active_test_started_at)
    paths = session_paths(_active_session_dir)
    paths["input_dir"].mkdir(parents=True, exist_ok=True)
    paths["screenshots_dir"].mkdir(parents=True, exist_ok=True)
    _write_session_manifest(status="started")
    return _active_session_dir, _active_test_started_at


def _write_session_manifest(
    status: str,
    input_result: dict[str, Any] | None = None,
    screen_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _active_session_dir or not _active_session_id or not _active_test_started_at:
        return {}

    paths = session_paths(_active_session_dir)
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "session_id": _active_session_id,
        "status": status,
        "test_started_at": _active_test_started_at,
        "updated_at": utc_now_iso(),
        "paths": stringify_paths(paths),
    }
    if input_result:
        data["input"] = _manifest_input_summary(input_result)
    if screen_result:
        data["screen"] = _manifest_screen_summary(screen_result)
    return write_manifest(_active_session_dir, data)


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


def _start_input_recording(config: InputRecordStart) -> dict[str, Any]:
    global _input_recorder, _input_thread, _input_start_error
    session_dir, started_at = _ensure_session(config.session_id)
    with _input_lock:
        if _input_recorder and _input_recorder.is_recording:
            raise HTTPException(400, "Input recorder is already recording")
        _input_recorder = create_input_recorder(config.backend, sample_hz=config.sample_hz)
        _input_start_error = None

        def run_input_recorder() -> None:
            global _input_start_error
            try:
                assert _input_recorder is not None
                _input_recorder.start()
            except BaseException as exc:
                _input_start_error = exc

        _input_thread = threading.Thread(target=run_input_recorder, daemon=True)
        _input_thread.start()

        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if _input_start_error:
                message = str(_input_start_error)
                raise HTTPException(
                    500,
                    f"Input recorder failed to start: {message}",
                )
            if _input_recorder.is_recording:
                break
            if not _input_thread.is_alive():
                raise HTTPException(500, "Input recorder stopped before startup completed")
            time.sleep(0.02)
    return {
        "status": "recording",
        "session_id": config.session_id,
        "backend": config.backend,
        "session_dir": str(session_dir),
        "test_started_at": started_at,
        "save_path": str(session_paths(session_dir)["input_path"]),
    }


def _stop_input_recording(session_id: str) -> dict[str, Any]:
    if not _input_recorder or not _input_recorder.is_recording:
        raise HTTPException(400, "Input recorder is not recording")
    _input_recorder.stop()
    if _input_thread:
        _input_thread.join(timeout=2.0)
    path = _input_path(session_id)
    result = _input_recorder.save(path, session_id)
    return {
        "status": "saved",
        "session_id": session_id,
        "path": str(path),
        "event_count": result["session"]["event_count"],
        "duration_sec": result["session"]["duration_sec"],
    }


def _start_screen_recording(config: ScreenStartRequest) -> dict[str, Any]:
    global _screen_recorder, _screen_thread
    session_dir, started_at = _ensure_session(config.session_id)
    with _screen_lock:
        if _screen_recorder.is_recording:
            raise HTTPException(400, "Screen recorder is already recording")
        _screen_recorder = ScreenRecorder(
            output_root=OUTPUT_ROOT,
            fps=config.fps,
            screenshot_callback_url=config.screenshot_callback_url,
        )
        locations = _screen_recorder.prepare(
            config.session_id,
            session_dir=session_dir,
            test_started_at=started_at,
        )
        _screen_thread = threading.Thread(
            target=_screen_recorder.start,
            args=(config.session_id,),
            daemon=True,
        )
        _screen_thread.start()
    return {
        "status": "recording",
        "session_id": config.session_id,
        "fps": config.fps,
        "session_dir": str(session_dir),
        "test_started_at": started_at,
        "locations": locations,
        "screenshot_callback_url": config.screenshot_callback_url,
    }


def _stop_screen_recording() -> dict[str, Any]:
    if not _screen_recorder.is_recording:
        raise HTTPException(400, "Screen recorder is not recording")
    _screen_recorder.stop()
    if _screen_thread:
        _screen_thread.join(timeout=5.0)
    summary = _screen_recorder.stop()
    return {"status": "saved", "summary": summary}


@app.post("/test/start")
def test_start(config: TestStartRequest) -> dict[str, Any]:
    input_result = _start_input_recording(
        InputRecordStart(
            session_id=config.session_id,
            backend=config.backend,
            sample_hz=config.sample_hz,
        )
    )
    try:
        screen_result = _start_screen_recording(
            ScreenStartRequest(
                session_id=config.session_id,
                fps=config.fps,
                screenshot_callback_url=config.screenshot_callback_url,
            )
        )
    except Exception:
        if _input_recorder and _input_recorder.is_recording:
            _input_recorder.stop()
            if _input_thread:
                _input_thread.join(timeout=2.0)
        raise
    return {
        "status": "started",
        "session_id": config.session_id,
        "input": input_result,
        "screen": screen_result,
    }


@app.post("/test/stop")
def test_stop(config: SessionRequest) -> dict[str, Any]:
    input_result = _stop_input_recording(config.session_id)
    screen_result = _stop_screen_recording()
    manifest = _write_session_manifest(
        status="stopped",
        input_result=input_result,
        screen_result=screen_result,
    )
    return {
        "status": "stopped",
        "session_id": config.session_id,
        "input": input_result,
        "screen": screen_result,
        "manifest": manifest,
    }


@app.post("/input/record/start")
def input_record_start(config: InputRecordStart) -> dict[str, Any]:
    return _start_input_recording(config)


@app.post("/input/record/stop")
def input_record_stop(config: SessionRequest) -> dict[str, Any]:
    input_result = _stop_input_recording(config.session_id)
    manifest = _write_session_manifest(status="input_saved", input_result=input_result)
    return {**input_result, "manifest": manifest}


@app.get("/input/recordings")
def list_input_recordings() -> dict[str, Any]:
    if not OUTPUT_ROOT.exists():
        return {"recordings": []}
    recordings = []
    for path in sorted(OUTPUT_ROOT.glob("*/input_recording/input.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        session = data.get("session", {})
        recordings.append({
            "session_id": session.get("session_id", path.stem),
            "path": str(path),
            "recorded_at": session.get("recorded_at"),
            "duration_sec": session.get("duration_sec"),
            "event_count": session.get("event_count"),
        })
    return {"recordings": recordings}


@app.post("/screen/record/start")
def screen_record_start(config: ScreenStartRequest) -> dict[str, Any]:
    return _start_screen_recording(config)


@app.post("/screen/record/stop")
def screen_record_stop() -> dict[str, Any]:
    screen_result = _stop_screen_recording()
    manifest = _write_session_manifest(status="screen_saved", screen_result=screen_result)
    return {**screen_result, "manifest": manifest}


@app.get("/screen/record/status")
def screen_record_status() -> dict[str, Any]:
    return {
        "recording": _screen_recorder.is_recording,
        "locations": _screen_recorder.locations,
    }


@app.post("/player/play")
def player_play(actions: list[dict[str, Any]]) -> dict[str, Any]:
    global _player_thread
    if not actions:
        raise HTTPException(400, "Action array is empty")
    with _player_lock:
        if _player.is_playing:
            raise HTTPException(400, "Player is already playing")

        def run() -> None:
            _player.play_actions(actions)

        _player_thread = threading.Thread(target=run, daemon=True)
        _player_thread.start()
    return {"status": "playing", "action_count": len(actions)}


@app.post("/player/play-file")
def player_play_file(req: PlayFileRequest) -> dict[str, Any]:
    global _player_thread
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"Action file not found: {path}")
    with _player_lock:
        if _player.is_playing:
            raise HTTPException(400, "Player is already playing")

        def run() -> None:
            _player.play_file(path)

        _player_thread = threading.Thread(target=run, daemon=True)
        _player_thread.start()
    return {"status": "playing", "path": str(path)}


@app.post("/player/stop")
def player_stop() -> dict[str, str]:
    if not _player.is_playing:
        raise HTTPException(400, "Player is not playing")
    _player.stop()
    return {"status": "stopped"}


@app.get("/status")
def status() -> dict[str, Any]:
    return {
        "input_recording": bool(_input_recorder and _input_recorder.is_recording),
        "screen_recording": _screen_recorder.is_recording,
        "playing": _player.is_playing,
        "screen_locations": _screen_recorder.locations,
    }
