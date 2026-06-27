"""FastAPI surface for input recording, screen recording, and JSON playback."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .input_logger import InputRecorder, create_input_recorder
from .player import ActionPlayer
from .screen_recorder import ScreenRecorder

OUTPUT_ROOT = Path("test_scenario_executor_output")
INPUT_RECORDINGS_DIR = OUTPUT_ROOT / "input_recordings"
SCREEN_RECORDINGS_DIR = OUTPUT_ROOT / "screen_recordings"

app = FastAPI(title="Test Scenario Executor API", version="1.0.0")

_input_recorder: InputRecorder | None = None
_input_thread: threading.Thread | None = None
_input_start_error: BaseException | None = None
_input_lock = threading.Lock()

_screen_recorder = ScreenRecorder(output_root=SCREEN_RECORDINGS_DIR, fps=30.0)
_screen_thread: threading.Thread | None = None
_screen_lock = threading.Lock()

_player = ActionPlayer(jitter_ms=0.0)
_player_thread: threading.Thread | None = None
_player_lock = threading.Lock()


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
    safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
    return INPUT_RECORDINGS_DIR / f"{safe_id}.json"


def _start_input_recording(config: InputRecordStart) -> dict[str, Any]:
    global _input_recorder, _input_thread, _input_start_error
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
        "save_path": str(_input_path(config.session_id)),
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
    with _screen_lock:
        if _screen_recorder.is_recording:
            raise HTTPException(400, "Screen recorder is already recording")
        _screen_recorder = ScreenRecorder(
            output_root=SCREEN_RECORDINGS_DIR,
            fps=config.fps,
            screenshot_callback_url=config.screenshot_callback_url,
        )
        locations = _screen_recorder.prepare(config.session_id)
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
        "locations": locations,
        "screenshot_callback_url": config.screenshot_callback_url,
    }


def _stop_screen_recording() -> dict[str, Any]:
    if not _screen_recorder.is_recording:
        raise HTTPException(400, "Screen recorder is not recording")
    _screen_recorder.stop()
    if _screen_thread:
        _screen_thread.join(timeout=5.0)
    manifest = _screen_recorder.stop()
    return {"status": "saved", "manifest": manifest}


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
    return {
        "status": "stopped",
        "session_id": config.session_id,
        "input": input_result,
        "screen": screen_result,
    }


@app.post("/input/record/start")
def input_record_start(config: InputRecordStart) -> dict[str, Any]:
    return _start_input_recording(config)


@app.post("/input/record/stop")
def input_record_stop(config: SessionRequest) -> dict[str, Any]:
    return _stop_input_recording(config.session_id)


@app.get("/input/recordings")
def list_input_recordings() -> dict[str, Any]:
    if not INPUT_RECORDINGS_DIR.exists():
        return {"recordings": []}
    recordings = []
    for path in sorted(INPUT_RECORDINGS_DIR.glob("*.json")):
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
    return _stop_screen_recording()


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
