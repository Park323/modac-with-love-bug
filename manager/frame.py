from dataclasses import dataclass


@dataclass
class Frame:
    timestamp_ms: int = 0
    png: bytes = b""
