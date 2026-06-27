from abc import ABC, abstractmethod

from manager.frame import Frame
from manager.clock import Clock
from manager.items import InputItem, InputResult


class IPlayModule(ABC):
    """Manager → Play. 본체는 타인이 구현."""

    @abstractmethod
    def begin(self, clock: Clock) -> None:
        ...

    @abstractmethod
    def dispatch(self, item: InputItem) -> None:
        ...

    @abstractmethod
    def end(self) -> list[InputResult]:
        ...


class ICaptureModule(ABC):
    """Manager가 프레임 받음 (공유 Clock ts). 본체는 타인이 구현."""

    @abstractmethod
    def begin(self, clock: Clock) -> None:
        ...

    @abstractmethod
    def next(self) -> Frame:
        ...

    @abstractmethod
    def end(self) -> None:
        ...


class IAnalysisModule(ABC):
    """Frame → 수행할 InputItem 결정. transport 무관 —
    로컬 in-process 구현 또는 원격 wss 래퍼 둘 다 이 계약을 만족."""

    @abstractmethod
    def analyze(self, frame: Frame) -> list[InputItem]:
        ...
