from test_scenario_executor.screen.recorder import ScreenRecorder


def test_latest_frame_initially_none():
    r = ScreenRecorder()
    assert r.latest_frame is None


def test_latest_frame_updates_via_internal_store():
    # grab 루프를 직접 돌리지 않고, 보관 경로(잠금 + property)만 검증.
    r = ScreenRecorder()
    sentinel = object()
    with r._frame_lock:
        r._latest_frame = sentinel
    assert r.latest_frame is sentinel
