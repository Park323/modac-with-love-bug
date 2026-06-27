import time

import pytest

from manager.autorun_controller import AutoRunController
from manager.clock import Clock
from manager.frame import Frame
from manager.items import InputItem, InputResult
from manager.modules import ICaptureModule, IPlayModule


class FakeCapture(ICaptureModule):
    def __init__(self, raise_on_begin=False, raise_on_next=False):
        self.began = 0
        self.ended = 0
        self._raise_begin = raise_on_begin
        self._raise_next = raise_on_next

    def begin(self, clock):
        if self._raise_begin:
            raise RuntimeError("begin boom")
        self.began += 1

    def next(self):
        if self._raise_next:
            raise RuntimeError("next boom")
        return Frame(timestamp_ms=1, bgr=object())

    def end(self):
        self.ended += 1


class RecordingPlay(IPlayModule):
    def __init__(self):
        self.began = 0
        self.items = []
        self.ended = 0

    def begin(self, clock):
        self.began += 1

    def dispatch(self, item):
        self.items.append(item)

    def end(self):
        self.ended += 1
        return [InputResult(i, 0, True) for i in self.items]


class FakeAnalysis:
    def __init__(self, finish_after=3, raise_times=0):
        self.finish_after = finish_after
        self.calls = 0
        self.wps = None
        self._raise_times = raise_times

    def set_waypoints(self, wps):
        self.wps = wps

    def analyze(self, frame):
        self.calls += 1
        if self.calls <= self._raise_times:
            raise ValueError("analyze boom")
        return [InputItem(key="W", action="key_down", raw={"type": "key_down"})]

    @property
    def done(self):
        return self.calls >= self.finish_after + self._raise_times

    @property
    def remaining(self):
        return max(0, (self.finish_after + self._raise_times) - self.calls)


class FakeLogger:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _wait_terminal(ctrl, timeout=5.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if ctrl.status()["state"] in ("done", "stopped", "error"):
            return
        time.sleep(0.01)


def test_normal_completion(monkeypatch):
    cap, play, ana, log = FakeCapture(), RecordingPlay(), FakeAnalysis(3), FakeLogger()
    ctrl = AutoRunController(cap, ana, play, Clock(), logger=log, fps=1000.0,
                             countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    _wait_terminal(ctrl)
    st = ctrl.status()
    assert st["state"] == "done"
    assert st["dispatched"] == 3
    assert cap.began == 1 and cap.ended == 1
    assert play.began == 1 and play.ended == 1
    assert log.started == 1 and log.stopped == 1
    assert ana.wps == [{"idx": 0, "x": 1, "y": 1, "rot": 0}]


def test_stop_sets_stopped():
    cap, play, ana = FakeCapture(), RecordingPlay(), FakeAnalysis(finish_after=10**9)
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0, countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    time.sleep(0.05)
    ctrl.stop()
    _wait_terminal(ctrl)
    assert ctrl.status()["state"] == "stopped"
    assert cap.ended == 1 and play.ended == 1


def test_begin_failure_sets_error():
    cap = FakeCapture(raise_on_begin=True)
    play, ana = RecordingPlay(), FakeAnalysis(3)
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0, countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    _wait_terminal(ctrl)
    st = ctrl.status()
    assert st["state"] == "error"
    assert "begin boom" in st["error"]
    assert cap.began == 0
    assert play.began == 1 and play.ended == 1   # play begin 성공 → end 호출


def test_tick_exception_is_resilient():
    cap, play = FakeCapture(), RecordingPlay()
    ana = FakeAnalysis(finish_after=2, raise_times=2)  # 처음 2틱 예외, 이후 정상
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0, countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    _wait_terminal(ctrl)
    st = ctrl.status()
    assert st["state"] == "done"
    assert st["dispatched"] == 2
    assert st["consecutive_errors"] == 0   # 정상 틱에서 리셋


def test_consecutive_error_cap():
    cap = FakeCapture(raise_on_next=True)
    play, ana = RecordingPlay(), FakeAnalysis(10**9)
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0,
                             max_consecutive_errors=5, countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    _wait_terminal(ctrl)
    st = ctrl.status()
    assert st["state"] == "error"
    assert st["consecutive_errors"] > 5
    assert cap.ended == 1


class FailingLogger:
    def __init__(self):
        self.stop_calls = 0

    def start(self):
        raise RuntimeError("logger boom")

    def stop(self):
        self.stop_calls += 1


def test_logger_start_failure_is_nonfatal():
    """A logger whose start() raises must not abort the capture→analyze→play loop."""
    cap, play, ana = FakeCapture(), RecordingPlay(), FakeAnalysis(finish_after=3)
    failing_log = FailingLogger()
    ctrl = AutoRunController(cap, ana, play, Clock(), logger=failing_log, fps=1000.0,
                             countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    _wait_terminal(ctrl)
    st = ctrl.status()
    assert st["state"] == "done"
    assert st["dispatched"] == 3
    assert play.ended == 1
    assert cap.ended == 1
    assert failing_log.stop_calls == 0   # never started → stop must NOT be called


def test_double_start_raises():
    cap, play, ana = FakeCapture(), RecordingPlay(), FakeAnalysis(finish_after=10**9)
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0, countdown_sec=0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    try:
        with pytest.raises(RuntimeError):
            ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    finally:
        ctrl.stop()
        _wait_terminal(ctrl)


def test_stop_during_countdown_aborts_before_begin():
    cap, play, ana = FakeCapture(), RecordingPlay(), FakeAnalysis(finish_after=10**9)
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0, countdown_sec=5.0)
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    ctrl.stop()                      # 카운트다운 도중 즉시 중단
    _wait_terminal(ctrl)
    st = ctrl.status()
    assert st["state"] == "stopped"
    assert play.began == 0           # begin 진입 안 함
    assert cap.began == 0


def test_countdown_delays_and_beeps():
    cap, play, ana = FakeCapture(), RecordingPlay(), FakeAnalysis(finish_after=1)
    beeps = []
    ctrl = AutoRunController(cap, ana, play, Clock(), fps=1000.0,
                             countdown_sec=2.0, beep_at_sec=3.0,
                             beeper=lambda hz, ms: beeps.append((hz, ms)))
    import time
    t0 = time.perf_counter()
    ctrl.start([{"idx": 0, "x": 1, "y": 1, "rot": 0}])
    _wait_terminal(ctrl, timeout=6.0)
    elapsed = time.perf_counter() - t0
    assert ctrl.status()["state"] == "done"
    assert elapsed >= 1.8            # 카운트다운 대기 반영
    assert len(beeps) >= 1           # 마지막 구간 부저
    assert play.began == 1           # 카운트다운 후 정상 시작
