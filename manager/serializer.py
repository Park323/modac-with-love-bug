import struct

from manager.frame import Frame


def serialize(frame: Frame) -> bytes:
    """[timestamp_ms: uint64 little-endian, 8 bytes][png raw bytes]"""
    return struct.pack("<Q", frame.timestamp_ms) + frame.png
