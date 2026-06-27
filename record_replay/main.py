from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.recorder import PollingRecorder
from src.replayer import InputReplayer
from src.detector import ScreenDetector

app = FastAPI(title="Modacthon QA Input API record_replay", version="0.2.0")

_recorder = PollingRecorder(sample_hz=120)
_replayer = InputReplayer(jitter_ms=2.0)
_detector = ScreenDetector(templates_dir="templates")

_record_thread: threading.Thread | None = None
_replay_thread: threading.Thread | None = None


class SessionConfig(BaseModel):
    session_id: str = "tdm_run_001"


class TemplateRequest(BaseModel):
    name: str


# ── recording ────────────────────────────────────────────────────────────────

@app.post("/record/start")
def record_start(config: SessionConfig):
    global _record_thread
    if _recorder.is_recording:
        raise HTTPException(400, "Already recording")
    _record_thread = threading.Thread(target=_recorder.start, daemon=True)
    _record_thread.start()
    return {"status": "recording", "session_id": config.session_id}


@app.post("/record/stop")
def record_stop(config: SessionConfig):
    if not _recorder.is_recording:
        raise HTTPException(400, "Not recording")
    _recorder.stop()
    if _record_thread:
        _record_thread.join(timeout=1.0)
    path = f"recordings/{config.session_id}.json"
    result = _recorder.save(path, config.session_id)
    return {
        "status": "saved",
        "path": path,
        "event_count": result["session"]["event_count"],
        "duration_sec": result["session"]["duration_sec"],
    }


# ── replay ───────────────────────────────────────────────────────────────────

@app.post("/replay/start")
def replay_start(config: SessionConfig):
    global _replay_thread
    if _replayer.is_replaying:
        raise HTTPException(400, "Already replaying")
    path = f"recordings/{config.session_id}.json"
    if not Path(path).exists():
        raise HTTPException(404, f"Recording not found: {path}")

    _replay_thread = threading.Thread(target=_replayer.replay, args=(path,), daemon=True)
    _replay_thread.start()
    return {"status": "replaying", "session_id": config.session_id}


@app.post("/replay/stop")
def replay_stop():
    if not _replayer.is_replaying:
        raise HTTPException(400, "Not replaying")
    _replayer.stop()
    return {"status": "stopped"}


# ── sessions ─────────────────────────────────────────────────────────────────

@app.get("/sessions")
def list_sessions():
    d = Path("recordings")
    if not d.exists():
        return {"sessions": []}
    sessions = []
    for f in sorted(d.glob("*.json")):
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
        s = data.get("session", {})
        sessions.append({
            "session_id": s.get("session_id"),
            "recorded_at": s.get("recorded_at"),
            "duration_sec": s.get("duration_sec"),
            "event_count": s.get("event_count"),
        })
    return {"sessions": sessions}


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    path = Path(f"recordings/{session_id}.json")
    if not path.exists():
        raise HTTPException(404, "Session not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── screen detection ─────────────────────────────────────────────────────────

@app.post("/templates/capture")
def capture_template(req: TemplateRequest):
    path = _detector.save_screenshot(req.name)
    return {"status": "saved", "path": path}


@app.get("/templates")
def list_templates():
    return {"templates": _detector.list_templates()}


@app.get("/screen/match/{template_name}")
def match_template(template_name: str):
    score = _detector.match(_detector.capture(), template_name)
    return {"template": template_name, "score": round(score, 4), "matched": score >= 0.80}


# ── status ───────────────────────────────────────────────────────────────────

@app.get("/status")
def status():
    return {
        "recording": _recorder.is_recording,
        "replaying": _replayer.is_replaying,
        "templates": _detector.list_templates(),
    }
