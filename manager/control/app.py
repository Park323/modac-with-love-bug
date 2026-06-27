import json
import os
import subprocess
import sys
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

_ROOT_DIR = Path(__file__).resolve().parents[2]
_UI_DIR = _ROOT_DIR / "ui"
_MOCK_RESULTS_DIR = _UI_DIR / "mock" / "results"
_CROSSFIRE_QA_DIR = _ROOT_DIR / "crossfire_qa"
_QA_INTERMEDIATE_DIR = _ROOT_DIR / "outputs" / "final_qa"
_QA_PACKAGE_DIR = _ROOT_DIR / "outputs" / "qa_review_package"

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


class PackageManifestRequest(BaseModel):
    resultDir: str


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


@app.post("/dashboard/analyze")
def dashboard_analyze(payload: DashboardAnalyzeRequest):
    if os.environ.get("LOVEBUG_UI_MOCK") == "1":
        return {"ok": True, "resultDir": str(_MOCK_RESULTS_DIR)}

    video_dir = payload.videoDirectory
    if not video_dir or not Path(video_dir).exists():
        raise HTTPException(status_code=400, detail="videoDirectory가 존재하지 않습니다.")

    py = sys.executable

    # 1단계: 영상 분석 → outputs/final_qa/
    run_cmd = [
        py, str(_CROSSFIRE_QA_DIR / "run.py"),
        "--dataset", video_dir,
        "--out", str(_QA_INTERMEDIATE_DIR),
        "--score-backend", "template",
        "--keep-going",
    ]
    result = subprocess.run(run_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"crossfire_qa/run.py 실패:\n{result.stderr[-2000:]}"
        )

    # 2단계: 리뷰 패키지 생성 → outputs/qa_review_package/
    pkg_cmd = [
        py, str(_CROSSFIRE_QA_DIR / "build_qa_review_package.py"),
        "--run-dir", str(_QA_INTERMEDIATE_DIR),
        "--out", str(_QA_PACKAGE_DIR),
        "--include-pass-details",
    ]
    result = subprocess.run(pkg_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"build_qa_review_package.py 실패:\n{result.stderr[-2000:]}"
        )

    return {"ok": True, "resultDir": str(_QA_PACKAGE_DIR)}


@app.post("/dashboard/package-manifest")
def dashboard_package_manifest(req: PackageManifestRequest):
    manifest_path = _safe_result_path(req.resultDir, "package_manifest.json")
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="package_manifest.json not found")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@app.get("/dashboard/artifact")
def dashboard_artifact(result_dir: str, path: str):
    artifact_path = _safe_result_path(result_dir, path)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(artifact_path)


# 정적 UI는 모든 API 라우트 등록 후 마지막에 마운트 (same-origin).
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
