"""FastAPI surface — all test_scenario_executor APIs + /auto-run/* endpoints."""

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
from .navigator.runner import AutoRunSession, parse_client_waypoints
from .playback.player import ActionPlayer
from .screen.recorder import ScreenRecorder

app = FastAPI(title="Test Auto Run Executor API", version="1.0.0")

# ── shared state ──────────────────────────────────────────────────────────────

_input_recorder:    InputRecorder | None  = None
_input_thread:      threading.Thread | None = None
_input_start_error: BaseException | None  = None
_input_lock = threading.Lock()

_screen_recorder = ScreenRecorder(output_root=OUTPUT_ROOT)
_screen_thread:  threading.Thread | None = None
_screen_lock = threading.Lock()

_player       = ActionPlayer(jitter_ms=0.0)
_player_thread: threading.Thread | None = None
_player_lock  = threading.Lock()

_auto_run_session = AutoRunSession()
_auto_run_lock    = threading.Lock()

_active_session_id:      str | None  = None
_active_session_dir:     Path | None = None
_active_test_started_at: str | None  = None


# ── request models ────────────────────────────────────────────────────────────

class InputRecordStart(BaseModel):
    session_id: str = "session"
    backend: Literal["hook", "polling"] = "hook"
    sample_hz: float = 120.0


class SessionRequest(BaseModel):
    session_id: str = "session"


class ScreenStartRequest(BaseModel):
    session_id: str = "session"
    screenshot_fps: float | None = None
    video_fps: float | None = None
    fps: float | None = None
    screenshot_callback_url: str | None = None


class TestStartRequest(BaseModel):
    session_id: str = "session"
    backend: Literal["hook", "polling"] = "hook"
    sample_hz: float = 120.0
    screenshot_fps: float | None = None
    video_fps: float | None = None
    fps: float | None = None
    screenshot_callback_url: str | None = None


class PlayFileRequest(BaseModel):
    path: str


class ClientWaypoint(BaseModel):
    idx: int
    x: float
    y: float
    rot: float


class AutoRunStartRequest(BaseModel):
    session_id: str = "auto_run_session"
    team: Literal["BL", "GR"] = "BL"
    waypoints: list[ClientWaypoint]
    screenshot_fps: float | None = None
    video_fps: float | None = None
    screenshot_callback_url: str | None = None


# ── session helpers ───────────────────────────────────────────────────────────

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

    _active_session_id      = session_id
    _active_test_started_at = utc_now_iso()
    _active_session_dir     = create_session_dir(session_id, OUTPUT_ROOT, _active_test_started_at)
    paths = session_paths(_active_session_dir)
    paths["input_dir"].mkdir(parents=True, exist_ok=True)
    paths["screenshots_dir"].mkdir(parents=True, exist_ok=True)
    _write_session_manifest(status="started")
    return _active_session_dir, _active_test_started_at


def _write_session_manifest(
    status: str,
    input_result:    dict[str, Any] | None = None,
    screen_result:   dict[str, Any] | None = None,
    auto_run_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _active_session_dir or not _active_session_id or not _active_test_started_at:
        return {}
    paths = session_paths(_active_session_dir)
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "session_id":     _active_session_id,
        "status":         status,
        "test_started_at": _active_test_started_at,
        "updated_at":     utc_now_iso(),
        "paths":          stringify_paths(paths),
    }
    if input_result:
        data["input"] = _manifest_input_summary(input_result)
    if screen_result:
        data["screen"] = _manifest_screen_summary(screen_result)
    if auto_run_result:
        data["auto_run"] = auto_run_result
    return write_manifest(_active_session_dir, data)


def _manifest_input_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {"path": r.get("path"), "event_count": r.get("event_count"), "duration_sec": r.get("duration_sec")}


def _manifest_screen_summary(r: dict[str, Any]) -> dict[str, Any]:
    s = r.get("summary") or r
    return {k: s.get(k) for k in (
        "screenshot_fps", "video_fps", "started_at", "stopped_at",
        "duration_sec", "screenshot_count", "video_frame_count", "screenshot_callback_url"
    )}


def _screen_fps(config: ScreenStartRequest | TestStartRequest | AutoRunStartRequest) -> tuple[float, float]:
    fallback = config.fps if hasattr(config, "fps") and config.fps is not None else 30.0
    ss_fps = config.screenshot_fps if config.screenshot_fps is not None else fallback
    v_fps  = config.video_fps      if config.video_fps      is not None else fallback
    if ss_fps <= 0:
        raise HTTPException(400, "screenshot_fps must be > 0")
    if v_fps <= 0:
        raise HTTPException(400, "video_fps must be > 0")
    return ss_fps, v_fps


# ── input recording helpers ───────────────────────────────────────────────────

def _start_input_recording(config: InputRecordStart) -> dict[str, Any]:
    global _input_recorder, _input_thread, _input_start_error
    session_dir, started_at = _ensure_session(config.session_id)
    with _input_lock:
        if _input_recorder and _input_recorder.is_recording:
            raise HTTPException(400, "Input recorder is already recording")
        _input_recorder    = create_input_recorder(config.backend, sample_hz=config.sample_hz)
        _input_start_error = None

        def run_recorder() -> None:
            global _input_start_error
            try:
                assert _input_recorder is not None
                _input_recorder.start()
            except BaseException as exc:
                _input_start_error = exc

        _input_thread = threading.Thread(target=run_recorder, daemon=True)
        _input_thread.start()

        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if _input_start_error:
                raise HTTPException(500, f"Input recorder failed to start: {_input_start_error}")
            if _input_recorder.is_recording:
                break
            if not _input_thread.is_alive():
                raise HTTPException(500, "Input recorder stopped before startup")
            time.sleep(0.02)
    return {
        "status":        "recording",
        "session_id":    config.session_id,
        "backend":       config.backend,
        "session_dir":   str(session_dir),
        "test_started_at": started_at,
        "save_path":     str(session_paths(session_dir)["input_path"]),
    }


def _stop_input_recording(session_id: str) -> dict[str, Any]:
    if not _input_recorder or not _input_recorder.is_recording:
        raise HTTPException(400, "Input recorder is not recording")
    _input_recorder.stop()
    if _input_thread:
        _input_thread.join(timeout=2.0)
    session_dir, _ = _ensure_session(session_id)
    path   = session_paths(session_dir)["input_path"]
    result = _input_recorder.save(path, session_id)
    return {
        "status":       "saved",
        "session_id":   session_id,
        "path":         str(path),
        "event_count":  result["session"]["event_count"],
        "duration_sec": result["session"]["duration_sec"],
    }


# ── screen recording helpers ──────────────────────────────────────────────────

def _start_screen_recording(config: ScreenStartRequest | AutoRunStartRequest) -> dict[str, Any]:
    global _screen_recorder, _screen_thread
    session_dir, started_at = _ensure_session(config.session_id)
    ss_fps, v_fps = _screen_fps(config)
    with _screen_lock:
        if _screen_recorder.is_recording:
            raise HTTPException(400, "Screen recorder is already recording")
        _screen_recorder = ScreenRecorder(
            output_root=OUTPUT_ROOT,
            screenshot_fps=ss_fps,
            video_fps=v_fps,
            screenshot_callback_url=config.screenshot_callback_url,
        )
        locations = _screen_recorder.prepare(config.session_id, session_dir=session_dir, test_started_at=started_at)
        _screen_thread = threading.Thread(target=_screen_recorder.start, args=(config.session_id,), daemon=True)
        _screen_thread.start()
    return {
        "status": "recording", "session_id": config.session_id,
        "screenshot_fps": ss_fps, "video_fps": v_fps,
        "session_dir": str(session_dir), "test_started_at": started_at,
        "locations": locations,
        "screenshot_callback_url": config.screenshot_callback_url,
    }


def _stop_screen_recording() -> dict[str, Any]:
    if not _screen_recorder.is_recording:
        raise HTTPException(400, "Screen recorder is not recording")
    _screen_recorder.stop()
    if _screen_thread:
        _screen_thread.join(timeout=5.0)
    return {"status": "saved", "summary": _screen_recorder.stop()}


# ── existing API endpoints (identical to test_scenario_executor) ──────────────

@app.post("/test/start")
def test_start(config: TestStartRequest) -> dict[str, Any]:
    input_result = _start_input_recording(
        InputRecordStart(session_id=config.session_id, backend=config.backend, sample_hz=config.sample_hz)
    )
    try:
        screen_result = _start_screen_recording(
            ScreenStartRequest(session_id=config.session_id, screenshot_fps=config.screenshot_fps,
                               video_fps=config.video_fps, fps=config.fps,
                               screenshot_callback_url=config.screenshot_callback_url)
        )
    except Exception:
        if _input_recorder and _input_recorder.is_recording:
            _input_recorder.stop()
            if _input_thread:
                _input_thread.join(timeout=2.0)
        raise
    return {"status": "started", "session_id": config.session_id, "input": input_result, "screen": screen_result}


@app.post("/test/stop")
def test_stop(config: SessionRequest) -> dict[str, Any]:
    input_result  = _stop_input_recording(config.session_id)
    screen_result = _stop_screen_recording()
    manifest = _write_session_manifest(status="stopped", input_result=input_result, screen_result=screen_result)
    return {"status": "stopped", "session_id": config.session_id,
            "input": input_result, "screen": screen_result, "manifest": manifest}


@app.post("/input/record/start")
def input_record_start(config: InputRecordStart) -> dict[str, Any]:
    return _start_input_recording(config)


@app.post("/input/record/stop")
def input_record_stop(config: SessionRequest) -> dict[str, Any]:
    result   = _stop_input_recording(config.session_id)
    manifest = _write_session_manifest(status="input_saved", input_result=result)
    return {**result, "manifest": manifest}


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
            "session_id":   session.get("session_id", path.stem),
            "path":         str(path),
            "recorded_at":  session.get("recorded_at"),
            "duration_sec": session.get("duration_sec"),
            "event_count":  session.get("event_count"),
        })
    return {"recordings": recordings}


@app.post("/screen/record/start")
def screen_record_start(config: ScreenStartRequest) -> dict[str, Any]:
    return _start_screen_recording(config)


@app.post("/screen/record/stop")
def screen_record_stop() -> dict[str, Any]:
    result   = _stop_screen_recording()
    manifest = _write_session_manifest(status="screen_saved", screen_result=result)
    return {**result, "manifest": manifest}


@app.get("/screen/record/status")
def screen_record_status() -> dict[str, Any]:
    return {"recording": _screen_recorder.is_recording, "locations": _screen_recorder.locations}


@app.post("/player/play")
def player_play(actions: list[dict[str, Any]]) -> dict[str, Any]:
    global _player_thread
    if not actions:
        raise HTTPException(400, "Action array is empty")
    with _player_lock:
        if _player.is_playing:
            raise HTTPException(400, "Player is already playing")
        _player_thread = threading.Thread(target=lambda: _player.play_actions(actions), daemon=True)
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
        _player_thread = threading.Thread(target=lambda: _player.play_file(path), daemon=True)
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
        "input_recording":  bool(_input_recorder and _input_recorder.is_recording),
        "screen_recording": _screen_recorder.is_recording,
        "playing":          _player.is_playing,
        "auto_run":         _auto_run_session.status,
        "screen_locations": _screen_recorder.locations,
    }


# ── auto-run endpoints (new) ──────────────────────────────────────────────────

@app.post("/auto-run/start")
def auto_run_start(config: AutoRunStartRequest) -> dict[str, Any]:
    """
    Start auto-run navigation from client waypoints.

    Request body:
      {
        "session_id": "run_001",
        "team": "BL",                        // "BL" | "GR"
        "waypoints": [
          {"idx": 0, "x": 227.4, "y": 217.3, "rot": 40.0},
          ...
        ],
        "screenshot_fps": 5,                 // optional
        "video_fps": 30,                     // optional
        "screenshot_callback_url": "..."     // optional
      }
    """
    with _auto_run_lock:
        if _auto_run_session.is_running:
            raise HTTPException(400, "Auto-run is already running")

        session_dir, started_at = _ensure_session(config.session_id)
        paths     = session_paths(session_dir)
        waypoints = parse_client_waypoints([w.model_dump() for w in config.waypoints])

        # output path for the navigator's own recording
        nav_output = str(paths["input_path"])

        # optionally start screen recording alongside
        screen_result = None
        if config.screenshot_fps or config.video_fps:
            try:
                screen_result = _start_screen_recording(config)
            except HTTPException:
                pass

        _auto_run_session.start(
            waypoints=waypoints,
            output_path=nav_output,
            session_id=config.session_id,
            team=config.team,
        )

        manifest = _write_session_manifest(
            status="auto_run_started",
            screen_result=screen_result,
            auto_run_result={"status": "running", "team": config.team, "waypoint_count": len(waypoints)},
        )

    return {
        "status":          "started",
        "session_id":      config.session_id,
        "team":            config.team,
        "waypoint_count":  len(waypoints),
        "nav_output_path": nav_output,
        "screen":          screen_result,
        "manifest":        manifest,
    }


@app.post("/auto-run/stop")
def auto_run_stop() -> dict[str, Any]:
    """Stop the running auto-run session and save recording."""
    with _auto_run_lock:
        if not _auto_run_session.is_running:
            raise HTTPException(400, "Auto-run is not running")
        _auto_run_session.stop()

    screen_result = None
    if _screen_recorder.is_recording:
        screen_result = _stop_screen_recording()

    summary  = _auto_run_session.summary()
    manifest = _write_session_manifest(
        status="auto_run_stopped",
        screen_result=screen_result,
        auto_run_result=summary,
    )
    return {"status": "stopped", "auto_run": summary, "screen": screen_result, "manifest": manifest}


@app.get("/auto-run/status")
def auto_run_status() -> dict[str, Any]:
    """Current auto-run status."""
    return _auto_run_session.summary()
