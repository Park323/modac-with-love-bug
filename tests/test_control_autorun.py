import time

import pytest
from fastapi.testclient import TestClient

from manager.control import app as app_module


@pytest.fixture
def client():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def reset(client):
    app_module.reset_controller()
    app_module.reset_recorder()
    app_module.reset_autorun()
    yield
    app_module.autorun.stop()
    # Wait for the background thread to finish so the next test (even in a
    # different module that doesn't reset_autorun) sees state != "running".
    for _ in range(200):
        if app_module.autorun.status()["state"] != "running":
            break
        time.sleep(0.01)


def _wp():
    return {"waypoints": [{"idx": 0, "x": 100, "y": 100, "rot": 0}]}


def test_auto_start_status_stop(client):
    r = client.post("/auto/start", json=_wp())
    assert r.status_code == 200
    assert r.json()["state"] == "running"

    st = client.get("/auto/status").json()
    assert st["state"] == "running"
    assert st["wp_total"] == 1

    r2 = client.post("/auto/stop")
    assert r2.status_code == 200
    for _ in range(200):
        if client.get("/auto/status").json()["state"] == "stopped":
            break
        time.sleep(0.01)
    assert client.get("/auto/status").json()["state"] == "stopped"


def test_auto_start_blocked_while_recording(client, monkeypatch):
    # recorder.is_recording True 로 강제
    monkeypatch.setattr(app_module.recorder.__class__, "is_recording",
                        property(lambda self: True))
    r = client.post("/auto/start", json=_wp())
    assert r.status_code == 409


def test_record_and_run_blocked_while_auto_running(client):
    client.post("/auto/start", json=_wp())
    assert client.get("/auto/status").json()["state"] == "running"
    r1 = client.post("/record/start", json={})
    assert r1.status_code == 409
    r2 = client.post("/run/start", json={"path": "x.json", "repeat": 1})
    assert r2.status_code == 409
