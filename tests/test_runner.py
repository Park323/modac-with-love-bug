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
