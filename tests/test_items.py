from manager.items import InputItem


def test_inputitem_backward_compatible_two_args():
    item = InputItem("D", "Pressed")
    assert item.key == "D"
    assert item.action == "Pressed"
    assert item.raw is None


def test_inputitem_carries_raw_event():
    ev = {"t": 0.1, "type": "mouse_move", "dx": 3}
    item = InputItem(key="mouse_move", action="", raw=ev)
    assert item.raw == ev
