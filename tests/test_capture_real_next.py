from manager.capture_real import RealCaptureModule
from manager.clock import Clock
from manager.frame import Frame


class _FakeRecorder:
    def __init__(self):
        self._latest = None

    @property
    def latest_frame(self):
        return self._latest


def test_next_returns_latest_frame():
    # [A/B] next()는 recorder.latest_frame(갓 잡은 최신 프레임)을 반환.
    cap = RealCaptureModule()
    cap._clock = Clock()
    cap._clock.start()
    rec = _FakeRecorder()
    cap._recorder = rec

    assert cap.next().bgr is None       # 아직 grab 전

    sentinel = object()
    rec._latest = sentinel
    f = cap.next()
    assert isinstance(f, Frame)
    assert f.bgr is sentinel
    assert f.timestamp_ms >= 0


def test_next_before_begin_returns_none_bgr():
    cap = RealCaptureModule()
    f = cap.next()                      # recorder/clock 없음
    assert isinstance(f, Frame)
    assert f.bgr is None
    assert f.timestamp_ms == 0


def test_on_frame_keeps_only_latest_in_queue():
    # 콜백 큐는 보존(미사용이지만 최신만 유지하는지 단위 검증).
    cap = RealCaptureModule()
    old, new = object(), object()
    cap._on_frame(old)
    cap._on_frame(new)
    assert cap._frame_q.get_nowait() is new


def test_default_fps_video_30_screenshot_10():
    cap = RealCaptureModule()
    assert cap._screenshot_fps == 10.0
    assert cap._video_fps == 30.0
