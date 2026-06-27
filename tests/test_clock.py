import time

from manager.clock import Clock


def test_now_ms_after_start_is_monotonic_and_absolute():
    c = Clock()
    c.start()
    a = c.now_ms()
    time.sleep(0.01)
    b = c.now_ms()
    assert a >= c.wall_start_ms
    assert b >= a
    assert b - a >= 5  # ~10ms 경과 반영 (여유)
