import sys
import types

import pytest

from manager.frame import Frame
from manager.items import InputItem


def _install_fake_auto_run(monkeypatch, position_fn, next_event_fn):
    """auto_run_action.position / .step 을 가짜 모듈로 주입."""
    pkg = types.ModuleType("auto_run_action")
    pos_mod = types.ModuleType("auto_run_action.position")
    step_mod = types.ModuleType("auto_run_action.step")
    pos_mod.get_position = position_fn
    step_mod.next_event = next_event_fn
    monkeypatch.setitem(sys.modules, "auto_run_action", pkg)
    monkeypatch.setitem(sys.modules, "auto_run_action.position", pos_mod)
    monkeypatch.setitem(sys.modules, "auto_run_action.step", step_mod)


def test_bgr_none_returns_empty(monkeypatch):
    called = {"pos": 0}

    def pos(frame):
        called["pos"] += 1
        return {"x": 0, "y": 0, "rot": 0}

    _install_fake_auto_run(monkeypatch, pos, lambda p, w=None: None)
    from manager.analysis_autorun import AutoRunAnalysis
    a = AutoRunAnalysis()
    a.set_waypoints([{"idx": 0, "x": 1, "y": 2, "rot": 0}])
    assert a.analyze(Frame(bgr=None)) == []
    assert called["pos"] == 0   # get_position 호출 안 함


def test_position_none_returns_empty(monkeypatch):
    _install_fake_auto_run(monkeypatch, lambda f: None,
                           lambda p, w=None: {"type": "key_down"})
    from manager.analysis_autorun import AutoRunAnalysis
    a = AutoRunAnalysis()
    a.set_waypoints([{"idx": 0, "x": 1, "y": 2, "rot": 0}])
    out = a.analyze(Frame(bgr=object()))
    assert out == []
    assert a.done is False


def test_event_wrapped_as_inputitem(monkeypatch):
    event = {"type": "key_down", "key": "W", "scan": 17, "extended": False}
    _install_fake_auto_run(monkeypatch, lambda f: {"x": 0, "y": 0, "rot": 0},
                           lambda p, w=None: event)
    from manager.analysis_autorun import AutoRunAnalysis
    a = AutoRunAnalysis()
    a.set_waypoints([{"idx": 0, "x": 1, "y": 2, "rot": 0}])
    out = a.analyze(Frame(bgr=object()))
    assert len(out) == 1
    assert isinstance(out[0], InputItem)
    assert out[0].raw is event
    assert out[0].action == "key_down"
    assert out[0].key == "W"


def test_waypoints_injected_only_once(monkeypatch):
    seen = []

    def ne(position, waypoints=None):
        seen.append(waypoints)
        return None

    _install_fake_auto_run(monkeypatch, lambda f: {"x": 0, "y": 0, "rot": 0}, ne)
    from manager.analysis_autorun import AutoRunAnalysis
    a = AutoRunAnalysis()
    wps = [{"idx": 0, "x": 1, "y": 2, "rot": 0}]
    a.set_waypoints(wps)
    a.analyze(Frame(bgr=object()))
    a.analyze(Frame(bgr=object()))
    assert seen[0] is not None and len(seen[0]) == 1   # 첫 호출 주입
    assert seen[1] is None                              # 이후 None


def test_done_when_waypoints_popped(monkeypatch):
    # next_event 가 실제처럼 주입 리스트를 pop 하도록 시뮬레이션.
    state = {"wps": None}

    def ne(position, waypoints=None):
        if waypoints:
            state["wps"] = waypoints
        if state["wps"]:
            state["wps"].pop(0)
        return None

    _install_fake_auto_run(monkeypatch, lambda f: {"x": 0, "y": 0, "rot": 0}, ne)
    from manager.analysis_autorun import AutoRunAnalysis
    a = AutoRunAnalysis()
    a.set_waypoints([{"idx": 0, "x": 1, "y": 2, "rot": 0}])
    assert a.remaining == 1
    assert a.done is False
    a.analyze(Frame(bgr=object()))     # pop → 0
    assert a.remaining == 0
    assert a.done is True
    assert a.analyze(Frame(bgr=object())) == []
