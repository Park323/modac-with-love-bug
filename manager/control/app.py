import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from manager.clock import Clock
from manager.play_stub import StubPlayModule
from manager.play_real import RealPlayModule
from manager.capture_real import RealCaptureModule
from manager.runner import RunController
from manager.control.dialog import pick_json_file, pick_directory
from manager.recorder_session import RecordSession, RecorderStartError

_UI_DIR = Path(__file__).resolve().parents[2] / "ui"
_PROJECT_DIR = _UI_DIR.parent
_MOCK_RESULTS_DIR = _UI_DIR / "mock" / "results"
_CROSSFIRE_RUNNER = _PROJECT_DIR / "crossfire_qa" / "run.py"
_CROSSFIRE_OUTPUT_DIR = _PROJECT_DIR / "crossfire_qa_output"

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


class DashboardAnalyzeRequest(BaseModel):
    project: str | None = None
    videoDirectory: str | None = None
    requestedAt: str | None = None


class FinalReportRequest(BaseModel):
    resultDir: str


def _safe_slug(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value.strip())
    return safe.strip("_") or "dataset"


def _safe_result_path(result_dir: str, child_path: str = "") -> Path:
    base = Path(result_dir).expanduser().resolve()
    target = (base / child_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid path")
    return target


@app.post("/scenario/browse")
def scenario_browse():
    return {"path": pick_json_file()}


@app.post("/record/start")
def record_start(req: RecordStartRequest):
    if controller.status()["state"] == "running":
        raise HTTPException(status_code=409, detail="run in progress")
    try:
        recorder.start(req.duration_sec)
    except RecorderStartError:
        raise HTTPException(status_code=503, detail="recorder failed to start")
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


@app.post("/dashboard/browse")
def dashboard_browse():
    return {"path": pick_directory()}


@app.get("/dashboard/health")
def dashboard_health():
    return {"ok": True, "report": "final_report.json"}


@app.post("/dashboard/analyze")
def dashboard_analyze(payload: DashboardAnalyzeRequest):
    if os.environ.get("LOVEBUG_UI_MOCK") == "1":
        return {"ok": True, "resultDir": str(_MOCK_RESULTS_DIR)}

    if not payload.videoDirectory:
        raise HTTPException(status_code=400, detail="videoDirectory is required")

    dataset = Path(payload.videoDirectory).expanduser().resolve()
    if not dataset.exists():
        raise HTTPException(status_code=404, detail=f"videoDirectory not found: {dataset}")

    if (dataset / "final_report.json").exists():
        return {"ok": True, "resultDir": str(dataset)}

    run_name = f"output_from_{_safe_slug(dataset.name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    result_dir = (_CROSSFIRE_OUTPUT_DIR / run_name / "run_output").resolve()
    result_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(_CROSSFIRE_RUNNER),
        "--dataset",
        str(dataset),
        "--out",
        str(result_dir),
        "--keep-going",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(_PROJECT_DIR),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to start analysis: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "analysis failed").strip()
        raise HTTPException(status_code=500, detail=detail[-4000:])

    final_report = result_dir / "final_report.json"
    if not final_report.exists():
        raise HTTPException(status_code=500, detail=f"analysis completed but final_report.json was not created: {final_report}")

    return {"ok": True, "resultDir": str(result_dir)}


@app.post("/dashboard/final-report")
def dashboard_final_report(req: FinalReportRequest):
    report_path = _safe_result_path(req.resultDir, "final_report.json")
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"final_report.json not found: {report_path}")
    return json.loads(report_path.read_text(encoding="utf-8"))


@app.get("/dashboard/artifact")
def dashboard_artifact(result_dir: str, path: str):
    artifact_path = _safe_result_path(result_dir, path)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(artifact_path)


# 정적 UI는 모든 API 라우트 등록 후 마지막에 마운트 (same-origin).
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
