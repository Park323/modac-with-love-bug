import pytest

import test_scenario_executor.input.win_input as wi
from manager.clock import Clock
from manager.items import InputItem
from manager.play_real import RealPlayModule


@pytest.fixture(autouse=True)
def no_real_cursor(monkeypatch):
    """begin()의 화면중심 이동이 테스트 중 실제 커서를 움직이지 않게 가로챔."""
    calls = []
    monkeypatch.setattr(wi, "move_cursor_to_center", lambda: (calls.append(True), (0, 0))[1])
    return calls


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


def test_begin_recenters_cursor(no_real_cursor):
    pm = RealPlayModule()
    pm.begin(_clock())
    assert no_real_cursor == [True]  # 재생 시작 시 커서 중심 이동 1회


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


def test_end_releases_held_keys(monkeypatch):
    import manager.play_real as pr
    dispatched = []

    class FakePlayer:
        def __init__(self, *a, **k): pass
        def _dispatch(self, action): dispatched.append(action)
        def stop(self): pass

    monkeypatch.setattr(
        "test_scenario_executor.playback.player.ActionPlayer", FakePlayer)

    m = pr.RealPlayModule()
    m.begin(Clock())
    m.dispatch(InputItem(key="W", action="key_down",
                         raw={"type": "key_down", "key": "W", "scan": 17, "extended": False}))
    m.end()

    ups = [a for a in dispatched if a.get("type") == "key_up"]
    assert len(ups) == 1
    assert ups[0].get("scan") == 17 or ups[0].get("key") == "W"


def test_end_no_release_when_key_already_up(monkeypatch):
    import manager.play_real as pr
    dispatched = []

    class FakePlayer:
        def __init__(self, *a, **k): pass
        def _dispatch(self, action): dispatched.append(action)
        def stop(self): pass

    monkeypatch.setattr(
        "test_scenario_executor.playback.player.ActionPlayer", FakePlayer)

    m = pr.RealPlayModule()
    m.begin(Clock())
    m.dispatch(InputItem(key="W", action="key_down",
                         raw={"type": "key_down", "key": "W", "scan": 17}))
    m.dispatch(InputItem(key="W", action="key_up",
                         raw={"type": "key_up", "key": "W", "scan": 17}))
    m.end()

    ups = [a for a in dispatched if a.get("type") == "key_up"]
    assert len(ups) == 1   # 명시적 key_up 1개뿐, end에서 추가 방출 없음
