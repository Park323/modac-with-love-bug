import json

import pytest

from manager.scenario import ScenarioReader


def _write(tmp_path, data):
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def test_read_returns_events_list(tmp_path):
    path = _write(tmp_path, {"events": [{"t": 0.1, "type": "mouse_move"},
                                        {"t": 0.2, "type": "key_down"}]})
    events = ScenarioReader.read(path)
    assert events == [{"t": 0.1, "type": "mouse_move"},
                      {"t": 0.2, "type": "key_down"}]


def test_read_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        ScenarioReader.read(str(tmp_path / "nope.json"))


def test_read_broken_json_raises_valueerror(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError):
        ScenarioReader.read(str(p))


def test_read_no_events_key_raises_valueerror(tmp_path):
    path = _write(tmp_path, {"session": {}})
    with pytest.raises(ValueError):
        ScenarioReader.read(path)


def test_read_empty_events_raises_valueerror(tmp_path):
    path = _write(tmp_path, {"events": []})
    with pytest.raises(ValueError):
        ScenarioReader.read(path)
