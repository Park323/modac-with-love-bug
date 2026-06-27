from abc import ABC, abstractmethod

from manager.frame import Frame


class IFrameSource(ABC):
    @abstractmethod
    def next(self) -> Frame:
        ...
