from manager.capture_real import RealCaptureModule
from manager.clock import Clock
from manager.frame import Frame


class _FakeRecorder:
    def __init__(self):
        self._latest = None

    @property
    def latest_frame(self):
        return self._latest


def test_next_returns_frame_with_latest_bgr():
    cap = RealCaptureModule()
    cap._clock = Clock()
    cap._clock.start()
    rec = _FakeRecorder()
    cap._recorder = rec

    f = cap.next()
    assert isinstance(f, Frame)
    assert f.bgr is None          # 아직 grab 전

    sentinel = object()
    rec._latest = sentinel
    f2 = cap.next()
    assert f2.bgr is sentinel
    assert f2.timestamp_ms >= 0


def test_next_before_begin_returns_none_bgr():
    cap = RealCaptureModule()
    f = cap.next()                # recorder/clock 없음
    assert isinstance(f, Frame)
    assert f.bgr is None
    assert f.timestamp_ms == 0


def test_default_screenshot_fps_is_10():
    cap = RealCaptureModule()
    assert cap._screenshot_fps == 10.0
