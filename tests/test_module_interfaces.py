import pytest

from manager.items import InputItem, InputResult
from manager.modules import IPlayModule, ICaptureModule, IAnalysisModule


def test_input_item_fields():
    it = InputItem(key="D", action="Pressed")
    assert it.key == "D"
    assert it.action == "Pressed"


def test_input_result_fields():
    it = InputItem(key="D", action="Pressed")
    r = InputResult(item=it, timestamp_ms=123, ok=True)
    assert r.item == it
    assert r.timestamp_ms == 123
    assert r.ok is True


def test_play_module_is_abstract():
    with pytest.raises(TypeError):
        IPlayModule()


def test_capture_module_is_abstract():
    with pytest.raises(TypeError):
        ICaptureModule()


def test_analysis_module_is_abstract():
    with pytest.raises(TypeError):
        IAnalysisModule()
