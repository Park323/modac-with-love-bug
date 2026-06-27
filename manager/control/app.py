from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from manager.clock import Clock
from manager.play_stub import StubPlayModule
from manager.runner import RunController
from manager.control.dialog import pick_json_file

_UI_DIR = Path(__file__).resolve().parents[2] / "ui"

app = FastAPI(title="QA PlayTest Manager Control", version="0.1.0")

controller = RunController(StubPlayModule(), Clock())


def reset_controller() -> None:
    """테스트용: 새 컨트롤러로 교체."""
    global controller
    controller = RunController(StubPlayModule(), Clock())


class StartRequest(BaseModel):
    path: str
    repeat: int = 1


@app.post("/scenario/browse")
def scenario_browse():
    return {"path": pick_json_file()}


@app.post("/run/start")
def run_start(req: StartRequest):
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
