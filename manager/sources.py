import time
from abc import ABC, abstractmethod

from manager.frame import Frame


class IFrameSource(ABC):
    @abstractmethod
    def next(self) -> Frame:
        ...


class FixedFileFrameSource(IFrameSource):
    def __init__(self, png_path: str):
        with open(png_path, "rb") as f:  # 없으면 FileNotFoundError
            self._png = f.read()

    def next(self) -> Frame:
        # MVP placeholder ts; 로드맵: 캡처 모듈이 진짜 ts 채움
        return Frame(timestamp_ms=int(time.time() * 1000), png=self._png)
