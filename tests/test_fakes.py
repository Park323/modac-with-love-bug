from manager.frame import Frame
from tests.fakes import FakeSource, FakeTransport


def test_fakes_basic_behavior():
    src = FakeSource(Frame(timestamp_ms=5, png=b"\x01"))
    tx = FakeTransport()

    assert tx.connect("ws://x") is True
    assert tx.connected_url == "ws://x"

    f = src.next()
    assert src.next_calls == 1
    assert tx.send(f) is True
    assert len(tx.sent) == 1
    assert tx.sent[0].timestamp_ms == 5
