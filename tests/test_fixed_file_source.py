import pytest

from manager.sources import FixedFileFrameSource


def test_loads_file_and_stamps_timestamp(tmp_path):
    data = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D])
    p = tmp_path / "fixed.png"
    p.write_bytes(data)

    src = FixedFileFrameSource(str(p))
    f = src.next()

    assert f.png == data
    assert f.timestamp_ms > 0


def test_two_next_calls_same_png_both_stamped(tmp_path):
    data = bytes([1, 2, 3, 4])
    p = tmp_path / "fixed.png"
    p.write_bytes(data)

    src = FixedFileFrameSource(str(p))
    a = src.next()
    b = src.next()

    assert a.png == b.png == data
    assert a.timestamp_ms > 0
    assert b.timestamp_ms > 0


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        FixedFileFrameSource("does_not_exist_12345.png")
