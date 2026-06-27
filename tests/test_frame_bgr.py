from manager.frame import Frame


def test_frame_defaults_bgr_none():
    f = Frame()
    assert f.timestamp_ms == 0
    assert f.png == b""
    assert f.bgr is None


def test_frame_carries_bgr_object():
    sentinel = object()
    f = Frame(timestamp_ms=5, png=b"x", bgr=sentinel)
    assert f.timestamp_ms == 5
    assert f.png == b"x"
    assert f.bgr is sentinel
