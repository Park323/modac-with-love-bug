from dataclasses import dataclass


@dataclass
class Frame:
    timestamp_ms: int = 0
    png: bytes = b""
    bgr: object | None = None   # 라이브 분석용 BGR ndarray (np.ndarray | None). numpy 강제 import 회피 위해 object.
