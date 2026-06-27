import json
import time
import threading

import pytest
from fastapi.testclient import TestClient

from manager.control import app as app_module


# ---------------------------------------------------------------------------
# Fake recorder — injected via reset_recorder() so NO real OS input is used.
# ---------------------------------------------------------------------------

class _FakeRec:
    def __init__(self, events=2):
        self._running = False
        self._n = events

    @property
    def is_recording(self):
        return self._running

    def start(self):
        self._running = True
        while self._running:
            time.sleep(0.005)

    def stop(self):
        self._running = False

    def save(self, path, session_id="session"):
        return {"session": {"event_count": self._n, "duration_sec": 0.5}}


def _fake_factory(events=2):
    def make(backend, sample_hz):
        return _FakeRec(events)
    return make


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def reset_controller():
    # 각 테스트마다 깨끗한 컨트롤러 + 가짜 recorder (실제 OS 입력 없음)
    app_module.reset_controller()
    app_module.reset_recorder(_fake_factory())
    yield


def _scenario(tmp_path, n):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"events": [{"t": i * 0.1, "type": "k"}
                                        for i in range(n)]}), encoding="utf-8")
    return str(p)


def _poll_until_done(client, timeout_polls=200):
    for _ in range(timeout_polls):
        st = client.get("/run/status").json()
        if st["state"] in ("done", "stopped", "error"):
            return st
        import time
        time.sleep(0.01)
    raise AssertionError("run did not finish")


def test_start_then_status_reaches_done(client, tmp_path):
    path = _scenario(tmp_path, 3)
    r = client.post("/run/start", json={"path": path, "repeat": 2})
    assert r.status_code == 200
    st = _poll_until_done(client)
    assert st["state"] == "done"
    assert st["total"] == 6


def test_start_bad_path_returns_400(client, tmp_path):
    r = client.post("/run/start",
                    json={"path": str(tmp_path / "nope.json"), "repeat": 1})
    assert r.status_code == 400


def test_duplicate_start_returns_409(client, tmp_path):
    import threading
    import time

    from manager.clock import Clock
    from manager.runner import RunController
    from manager.play_stub import StubPlayModule

    entered = threading.Event()
    gate = threading.Event()

    class BlockingPlay(StubPlayModule):
        def dispatch(self, item):
            entered.set()
            gate.wait(2.0)          # 첫 dispatch에서 블록 → 실행 상태 유지
            super().dispatch(item)

    app_module.controller = RunController(BlockingPlay(), Clock())
    path = _scenario(tmp_path, 5)
    try:
        r1 = client.post("/run/start", json={"path": path, "repeat": 1})
        assert r1.status_code == 200
        assert entered.wait(2.0)    # 워커가 첫 dispatch에 진입할 때까지 대기
        r2 = client.post("/run/start", json={"path": path, "repeat": 1})
        assert r2.status_code == 409
    finally:
        gate.set()
        app_module.controller.stop()
        for _ in range(200):
            if app_module.controller.status()["state"] in ("done", "stopped", "error"):
                break
            time.sleep(0.01)


def test_browse_returns_path(client, monkeypatch):
    monkeypatch.setattr(app_module, "pick_json_file", lambda: "C:/x/y.json")
    r = client.post("/scenario/browse")
    assert r.status_code == 200
    assert r.json()["path"] == "C:/x/y.json"


def test_browse_cancel_returns_null(client, monkeypatch):
    monkeypatch.setattr(app_module, "pick_json_file", lambda: None)
    r = client.post("/scenario/browse")
    assert r.status_code == 200
    assert r.json()["path"] is None


def test_stop_sets_stopped(client, tmp_path):
    big = _scenario(tmp_path, 5000)
    client.post("/run/start", json={"path": big, "repeat": 1})
    client.post("/run/stop")
    st = _poll_until_done(client)
    assert st["state"] in ("stopped", "done")


def test_start_directory_path_returns_400(client, tmp_path):
    # 디렉터리 경로 → open()이 OSError 계열 → 400
    r = client.post("/run/start", json={"path": str(tmp_path), "repeat": 1})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /record/* endpoint tests
# ---------------------------------------------------------------------------

def test_record_start_stop_cycle(client):
    r = client.post("/record/start", json={"duration_sec": None})
    assert r.status_code == 200
    assert r.json()["state"] == "recording"

    st = client.get("/record/status").json()
    assert st["state"] == "recording"

    r2 = client.post("/record/stop")
    assert r2.status_code == 200
    data = r2.json()
    assert data["state"] == "done"
    assert data["event_count"] == 2


def test_record_start_refused_while_run_active(client, tmp_path):
    from manager.clock import Clock
    from manager.runner import RunController
    from manager.play_stub import StubPlayModule

    entered = threading.Event()
    gate = threading.Event()

    class BlockingPlay(StubPlayModule):
        def dispatch(self, item):
            entered.set()
            gate.wait(2.0)
            super().dispatch(item)

    app_module.controller = RunController(BlockingPlay(), Clock())
    path = _scenario(tmp_path, 5)
    try:
        r1 = client.post("/run/start", json={"path": path, "repeat": 1})
        assert r1.status_code == 200
        assert entered.wait(2.0)
        r2 = client.post("/record/start", json={"duration_sec": None})
        assert r2.status_code == 409
    finally:
        gate.set()
        app_module.controller.stop()
        for _ in range(200):
            if app_module.controller.status()["state"] in ("done", "stopped", "error"):
                break
            time.sleep(0.01)


def test_run_start_refused_while_recording(client, tmp_path):
    try:
        r = client.post("/record/start", json={"duration_sec": None})
        assert r.status_code == 200
        assert r.json()["state"] == "recording"

        path = _scenario(tmp_path, 5)
        r2 = client.post("/run/start", json={"path": path, "repeat": 1})
        assert r2.status_code == 409
    finally:
        client.post("/record/stop")
