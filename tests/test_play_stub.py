import pytest

from manager.clock import Clock
from manager.items import InputItem
from manager.play_stub import StubPlayModule


def _clock():
    c = Clock()
    c.start()
    return c


def test_begin_dispatch_end_returns_result_per_dispatch():
    play = StubPlayModule()
    play.begin(_clock())
    play.dispatch(InputItem("a", "", {"t": 0.1}))
    play.dispatch(InputItem("b", "", {"t": 0.2}))
    results = play.end()
    assert len(results) == 2
    assert all(r.ok for r in results)
    assert results[0].item.key == "a"
    assert results[1].item.key == "b"


def test_results_have_timestamp_from_clock():
    play = StubPlayModule()
    play.begin(_clock())
    play.dispatch(InputItem("a", ""))
    results = play.end()
    assert results[0].timestamp_ms > 0


def test_dispatch_before_begin_raises():
    play = StubPlayModule()
    with pytest.raises(RuntimeError):
        play.dispatch(InputItem("a", ""))
