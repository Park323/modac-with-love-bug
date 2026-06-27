from manager.capture_real import RealCaptureModule
from manager.clock import Clock
from manager.frame import Frame


def test_next_returns_pushed_callback_frame():
    cap = RealCaptureModule()
    cap._clock = Clock()
    cap._clock.start()

    sentinel = object()
    cap._on_frame(sentinel)       # ScreenRecorder 스샷 콜백 시뮬레이션
    f = cap.next()
    assert isinstance(f, Frame)
    assert f.bgr is sentinel
    assert f.timestamp_ms >= 0


def test_next_returns_none_when_no_frame_pushed():
    cap = RealCaptureModule()
    cap._clock = Clock()
    cap._clock.start()
    f = cap.next()                # 큐 비어있음 → 0.2s timeout 후 None
    assert isinstance(f, Frame)
    assert f.bgr is None


def test_on_frame_keeps_only_latest():
    cap = RealCaptureModule()
    cap._clock = Clock()
    cap._clock.start()
    old, new = object(), object()
    cap._on_frame(old)
    cap._on_frame(new)            # 최신만 유지(old 드롭)
    assert cap.next().bgr is new


def test_next_before_begin_returns_none_bgr():
    cap = RealCaptureModule()
    f = cap.next()                # recorder/clock 없음, 큐 비어있음
    assert isinstance(f, Frame)
    assert f.bgr is None
    assert f.timestamp_ms == 0


def test_default_fps_video_30_screenshot_10():
    cap = RealCaptureModule()
    assert cap._screenshot_fps == 10.0
    assert cap._video_fps == 30.0
