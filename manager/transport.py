from abc import ABC, abstractmethod

from manager.frame import Frame


class IServerTransport(ABC):
    @abstractmethod
    def connect(self, url: str) -> bool:
        ...

    @abstractmethod
    def send(self, frame: Frame) -> bool:
        ...

    @abstractmethod
    def close(self) -> None:
        ...
