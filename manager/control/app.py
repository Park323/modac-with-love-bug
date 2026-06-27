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

# App-level guard: True from the moment start() succeeds until the run finishes
# or is stopped.  RunController.start() reads the scenario file *outside* its
# internal lock, so on fast machines the background thread can complete before
# RunController._running is re-checked on a second start() call.
# _run_active is cleared by /run/status (state done/stopped/error) and /run/stop.
_run_active = False


def reset_controller() -> None:
    """테스트용: 새 컨트롤러로 교체."""
    global controller, _run_active
    controller = RunController(StubPlayModule(), Clock())
    _run_active = False


class StartRequest(BaseModel):
    path: str
    repeat: int = 1


@app.post("/scenario/browse")
def scenario_browse():
    return {"path": pick_json_file()}


@app.post("/run/start")
def run_start(req: StartRequest):
    global _run_active
    if _run_active:
        raise HTTPException(status_code=409, detail="already running")
    try:
        controller.start(req.path, req.repeat)
    except RuntimeError:
        raise HTTPException(status_code=409, detail="already running")
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    _run_active = True
    return {"state": controller.status()["state"]}


@app.get("/run/status")
def run_status():
    global _run_active
    st = controller.status()
    if st["state"] in ("done", "stopped", "error"):
        _run_active = False
    return st


@app.post("/run/stop")
def run_stop():
    global _run_active
    controller.stop()
    _run_active = False
    return {"state": controller.status()["state"]}


# 정적 UI는 모든 API 라우트 등록 후 마지막에 마운트 (same-origin).
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
