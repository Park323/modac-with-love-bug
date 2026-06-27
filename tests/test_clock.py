import time

from manager.clock import Clock


def test_now_ms_after_start_is_monotonic_and_absolute():
    c = Clock()
    c.start()
    a = c.now_ms()
    time.sleep(0.05)  # Windows 모노토닉 분해능(~15ms) 여유
    b = c.now_ms()
    assert a >= c.wall_start_ms
    assert b >= a
    assert b - a >= 1  # 경과 시간 반영(분해능 고려해 느슨하게)
