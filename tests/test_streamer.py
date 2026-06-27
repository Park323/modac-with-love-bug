from manager.frame import Frame
from manager.streamer import FrameStreamer
from tests.fakes import FakeSource, FakeTransport


def test_tick_pulls_one_frame_and_sends():
    src = FakeSource(Frame(timestamp_ms=42, png=b"\x01\x02\x03"))
    tx = FakeTransport()
    streamer = FrameStreamer(src, tx, fps=30)

    streamer.tick()

    assert src.next_calls == 1
    assert len(tx.sent) == 1
    assert tx.sent[0].timestamp_ms == 42
    assert tx.sent[0].png == b"\x01\x02\x03"


def test_run_loops_until_stop():
    src = FakeSource(Frame(timestamp_ms=1, png=b""))
    tx = FakeTransport()
    streamer = FrameStreamer(src, tx, fps=1000)  # 1ms 간격

    def stop_after_three(_frame):
        if len(tx.sent) >= 3:
            streamer.stop()

    tx.on_send = stop_after_three
    streamer.run()

    assert len(tx.sent) == 3
