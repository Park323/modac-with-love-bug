import json
import threading

import pytest

from manager.clock import Clock
from manager.items import InputItem, InputResult
from manager.modules import IPlayModule
from manager.runner import RunController


class RecordingPlay(IPlayModule):
    def __init__(self):
        self.begun = 0
        self.items = []
        self.ended = 0
        self.lock = threading.Lock()

    def begin(self, clock):
        self.begun += 1

    def dispatch(self, item):
        with self.lock:
            self.items.append(item)

    def end(self):
        self.ended += 1
        return [InputResult(i, 0, True) for i in self.items]


def _scenario(tmp_path, n):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"events": [{"t": i * 0.1, "type": "k"}
                                        for i in range(n)]}), encoding="utf-8")
    return str(p)


def _wait_done(ctrl, timeout=5.0):
    ctrl._thread.join(timeout)


def test_repeat_dispatches_events_times_repeat(tmp_path):
    play = RecordingPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 3), repeat=4)
    _wait_done(ctrl)
    assert len(play.items) == 12
    assert play.begun == 1
    assert play.ended == 1
    st = ctrl.status()
    assert st["state"] == "done"
    assert st["total"] == 12


def test_dispatch_carries_raw_event(tmp_path):
    play = RecordingPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 2), repeat=1)
    _wait_done(ctrl)
    assert play.items[0].raw == {"t": 0.0, "type": "k"}
    assert play.items[0].key == "k"


def test_repeat_zero_normalized_to_one(tmp_path):
    play = RecordingPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 5), repeat=0)
    _wait_done(ctrl)
    assert len(play.items) == 5
    assert ctrl.status()["repeat"] == 1


def test_start_while_running_raises(tmp_path):
    # 느린 play로 running 상태 유지
    class SlowPlay(RecordingPlay):
        def dispatch(self, item):
            super().dispatch(item)
            import time
            time.sleep(0.05)

    play = SlowPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 50), repeat=1)
    try:
        with pytest.raises(RuntimeError):
            ctrl.start(_scenario(tmp_path, 1), repeat=1)
    finally:
        ctrl.stop()
        ctrl._thread.join(5.0)


def test_stop_aborts_early(tmp_path):
    class SlowPlay(RecordingPlay):
        def dispatch(self, item):
            super().dispatch(item)
            import time
            time.sleep(0.02)

    play = SlowPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 1000), repeat=1)
    import time
    time.sleep(0.1)
    ctrl.stop()
    ctrl._thread.join(5.0)
    assert ctrl.status()["state"] == "stopped"
    assert len(play.items) < 1000


def test_bad_path_propagates_on_start(tmp_path):
    play = RecordingPlay()
    ctrl = RunController(play, Clock())
    with pytest.raises(FileNotFoundError):
        ctrl.start(str(tmp_path / "nope.json"), repeat=1)


def test_worker_exception_sets_error_state(tmp_path):
    class BoomPlay(RecordingPlay):
        def dispatch(self, item):
            raise ValueError("boom")

    play = BoomPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 3), repeat=1)
    ctrl._thread.join(5.0)
    st = ctrl.status()
    assert st["state"] == "error"
    assert "boom" in st["error"]


def test_concurrent_start_only_one_runs(tmp_path):
    class SlowPlay(RecordingPlay):
        def dispatch(self, item):
            super().dispatch(item)
            import time
            time.sleep(0.02)

    play = SlowPlay()
    ctrl = RunController(play, Clock())
    path = _scenario(tmp_path, 100)
    errors = []

    def go():
        try:
            ctrl.start(path, 1)
        except RuntimeError:
            errors.append(1)

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert errors.count(1) == 4  # 1개만 성공, 4개 RuntimeError
        assert ctrl.status()["state"] == "running"
    finally:
        ctrl.stop()
        ctrl._thread.join(5.0)


def test_full_completion_always_done(tmp_path):
    play = RecordingPlay()
    ctrl = RunController(play, Clock())
    ctrl.start(_scenario(tmp_path, 3), repeat=2)
    _wait_done(ctrl)
    assert ctrl.status()["state"] == "done"
    assert len(play.items) == 6


def _timed_scenario(tmp_path, ts):
    p = tmp_path / "timed.json"
    p.write_text(json.dumps({"events": [{"t": t, "type": "k"} for t in ts]}),
                 encoding="utf-8")
    return str(p)


def test_realtime_paces_to_event_timestamps(tmp_path):
    import time
    play = RecordingPlay()
    ctrl = RunController(play, Clock(), realtime=True)
    t0 = time.perf_counter()
    ctrl.start(_timed_scenario(tmp_path, [0.0, 0.2, 0.4]), repeat=1)
    _wait_done(ctrl)
    elapsed = time.perf_counter() - t0
    assert len(play.items) == 3
    assert elapsed >= 0.35  # 마지막 이벤트 t=0.4 만큼 대기 반영(여유)


def test_realtime_false_is_instant(tmp_path):
    import time
    play = RecordingPlay()
    ctrl = RunController(play, Clock(), realtime=False)
    t0 = time.perf_counter()
    ctrl.start(_timed_scenario(tmp_path, [0.0, 0.5, 1.0]), repeat=1)
    _wait_done(ctrl)
    elapsed = time.perf_counter() - t0
    assert len(play.items) == 3
    assert elapsed < 0.2  # 타이밍 무시 → 즉시


def test_stop_interrupts_long_wait(tmp_path):
    import time
    play = RecordingPlay()
    ctrl = RunController(play, Clock(), realtime=True)
    # 첫 이벤트 즉시, 둘째는 100초 뒤 → 긴 대기 중 stop이 즉시 끊어야 함
    ctrl.start(_timed_scenario(tmp_path, [0.0, 100.0]), repeat=1)
    for _ in range(200):  # 첫 dispatch 발생까지 대기
        if ctrl.status()["item_index"] >= 1:
            break
        time.sleep(0.01)
    ctrl.stop()
    t0 = time.perf_counter()
    ctrl._thread.join(3.0)
    assert (time.perf_counter() - t0) < 1.0  # 100초 안 기다리고 즉시 종료
    assert ctrl.status()["state"] == "stopped"
    assert len(play.items) == 1
