import numpy as np

from modac.protocol import Action, decode_frame, encode_frame


def test_frame_roundtrip_preserves_shape():
    frame = np.random.randint(0, 256, (90, 160, 3), dtype=np.uint8)
    out = decode_frame(encode_frame(frame))
    assert out.shape == frame.shape
    assert out.dtype == np.uint8


def test_action_defaults_are_idle():
    a = Action.idle()
    assert a.forward is False and a.fire is False
    assert a.yaw == 0.0 and a.pitch == 0.0
    assert a.weapon == 0


def test_action_serializes_to_dict():
    a = Action(forward=True, yaw=12.5, weapon=3)
    d = a.model_dump()
    assert d["forward"] is True and d["yaw"] == 12.5 and d["weapon"] == 3
    assert Action(**d) == a
