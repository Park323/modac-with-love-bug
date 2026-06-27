import time
import pytest
from manager.recorder_session import RecordSession


class FakeRecorder:
    def __init__(self, events=2):
        self._running = False
        self._n = events
        self.save_calls = 0
        self.saved_path = None

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
        self.save_calls += 1
        self.saved_path = str(path)
        return {"session": {"event_count": self._n, "duration_sec": 1.23}}


def _factory(events=2):
    holder = {}
    def make(backend, sample_hz):
        r = FakeRecorder(events)
        holder["r"] = r
        return r
    make.holder = holder
    return make


def test_status_idle_before_start(tmp_path):
    rs = RecordSession(recorder_factory=_factory(), output_root=tmp_path)
    assert rs.status()["state"] == "idle"


def test_start_then_stop_saves(tmp_path):
    f = _factory(3)
    rs = RecordSession(recorder_factory=f, output_root=tmp_path)
    rs.start()
    rs.stop()
    st = rs.status()
    assert st["state"] == "done"
    assert st["event_count"] == 3
    assert st["path"] and st["path"].endswith(".json")
    assert f.holder["r"].save_calls == 1


def test_start_while_recording_raises(tmp_path):
    rs = RecordSession(recorder_factory=_factory(), output_root=tmp_path)
    rs.start()
    try:
        with pytest.raises(RuntimeError):
            rs.start()
    finally:
        rs.stop()


def test_stop_without_events_is_done(tmp_path):
    rs = RecordSession(recorder_factory=_factory(0), output_root=tmp_path)
    rs.start()
    rs.stop()
    st = rs.status()
    assert st["state"] == "done"
    assert st["event_count"] == 0


def test_stop_idempotent(tmp_path):
    f = _factory(1)
    rs = RecordSession(recorder_factory=f, output_root=tmp_path)
    rs.start()
    rs.stop()
    rs.stop()
    assert f.holder["r"].save_calls == 1


def test_duration_auto_stops(tmp_path):
    rs = RecordSession(recorder_factory=_factory(2), output_root=tmp_path)
    rs.start(duration_sec=0.1)
    for _ in range(200):
        if rs.status()["state"] == "done":
            break
        time.sleep(0.01)
    assert rs.status()["state"] == "done"
