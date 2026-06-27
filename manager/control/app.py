from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from manager.clock import Clock
from manager.play_stub import StubPlayModule
from manager.play_real import RealPlayModule
from manager.capture_real import RealCaptureModule
from manager.runner import RunController
from manager.control.dialog import pick_json_file
from manager.recorder_session import RecordSession

_UI_DIR = Path(__file__).resolve().parents[2] / "ui"

app = FastAPI(title="QA PlayTest Manager Control", version="0.1.0")

# 프로덕션: 실제 Play(OS 입력) + Capture(화면 녹화). realtime 페이싱.
controller = RunController(
    RealPlayModule(), Clock(), realtime=True, capture=RealCaptureModule())

recorder = RecordSession()


def reset_controller() -> None:
    """테스트용: 실제 OS 입력/녹화 없는 Stub 컨트롤러로 교체."""
    global controller
    controller = RunController(StubPlayModule(), Clock(), realtime=False)


def reset_recorder(factory=None) -> None:
    """테스트용: 가짜 팩토리로 recorder 교체 (실제 OS 입력 없음)."""
    global recorder
    if factory is not None:
        recorder = RecordSession(recorder_factory=factory)
    else:
        recorder = RecordSession()


class StartRequest(BaseModel):
    path: str
    repeat: int = 1


class RecordStartRequest(BaseModel):
    duration_sec: float | None = None


@app.post("/scenario/browse")
def scenario_browse():
    return {"path": pick_json_file()}


@app.post("/record/start")
def record_start(req: RecordStartRequest):
    if controller.status()["state"] == "running":
        raise HTTPException(status_code=409, detail="run in progress")
    try:
        recorder.start(req.duration_sec)
    except RuntimeError:
        raise HTTPException(status_code=409, detail="already recording")
    return {"state": recorder.status()["state"]}


@app.post("/record/stop")
def record_stop():
    recorder.stop()
    return recorder.status()


@app.get("/record/status")
def record_status():
    return recorder.status()


@app.post("/run/start")
def run_start(req: StartRequest):
    if recorder.is_recording:
        raise HTTPException(status_code=409, detail="recording in progress")
    try:
        controller.start(req.path, req.repeat)
    except RuntimeError:
        raise HTTPException(status_code=409, detail="already running")
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"state": controller.status()["state"]}


@app.get("/run/status")
def run_status():
    return controller.status()


@app.post("/run/stop")
def run_stop():
    controller.stop()
    return {"state": controller.status()["state"]}


# 정적 UI는 모든 API 라우트 등록 후 마지막에 마운트 (same-origin).
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
