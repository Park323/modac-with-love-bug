import time

import test_scenario_executor.screen.recorder as rec_mod

from manager.capture_real import RealCaptureModule
from manager.clock import Clock


class _FakeRecorder:
    def __init__(self, *args, **kwargs):
        self.prepared = None
        self.started = None
        self.stopped = False
        self._running = False

    @property
    def is_recording(self):
        return self._running

    def prepare(self, session_id, session_dir=None, test_started_at=None):
        self.prepared = session_id
        return {"session_dir": "fake"}

    def start(self, session_id):
        self.started = session_id
        self._running = True
        while self._running:
            time.sleep(0.005)

    def stop(self):
        self._running = False
        self.stopped = True
        return {"summary": "ok"}


def test_capture_begin_starts_and_end_stops(monkeypatch):
    created = {}

    def factory(*args, **kwargs):
        r = _FakeRecorder()
        created["r"] = r
        return r

    monkeypatch.setattr(rec_mod, "ScreenRecorder", factory)

    cap = RealCaptureModule()
    cap.begin(Clock())
    for _ in range(200):  # 녹화 스레드 시작 대기
        if created["r"].started is not None:
            break
        time.sleep(0.005)
    assert created["r"].prepared is not None
    assert created["r"].started is not None

    cap.end()
    assert created["r"].stopped is True
    assert cap.summary == {"summary": "ok"}
