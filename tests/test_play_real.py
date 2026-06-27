import pytest

from manager.clock import Clock
from manager.items import InputItem
from manager.play_real import RealPlayModule


def _clock():
    c = Clock()
    c.start()
    return c


def test_dispatch_forwards_raw_event_to_player():
    pm = RealPlayModule()
    pm.begin(_clock())
    calls = []
    pm._player._dispatch = lambda a: calls.append(a)  # 실제 OS 입력 방지
    ev = {"t": 0.0, "type": "mouse_move", "dx": 3, "dy": -2}
    pm.dispatch(InputItem(key="mouse_move", action="", raw=ev))
    assert calls == [ev]


def test_dispatch_before_begin_raises():
    pm = RealPlayModule()
    with pytest.raises(RuntimeError):
        pm.dispatch(InputItem("a", ""))


def test_end_returns_result_per_dispatch():
    pm = RealPlayModule()
    pm.begin(_clock())
    pm._player._dispatch = lambda a: None
    pm.dispatch(InputItem("a", "", {"type": "x"}))
    pm.dispatch(InputItem("b", "", {"type": "y"}))
    results = pm.end()
    assert len(results) == 2
    assert all(r.ok for r in results)
