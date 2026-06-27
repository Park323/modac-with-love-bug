from manager.frame import Frame
from manager.serializer import serialize

def test_serialize_writes_le_timestamp_then_png():
    f = Frame(timestamp_ms=0x0102030405060708, png=bytes([0xAA, 0xBB, 0xCC]))
    out = serialize(f)
    assert len(out) == 8 + 3
    assert out[:8] == bytes([0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01])
    assert out[8:] == bytes([0xAA, 0xBB, 0xCC])

def test_serialize_empty_png():
    f = Frame(timestamp_ms=0, png=b"")
    assert serialize(f) == bytes(8)
