from abc import ABC, abstractmethod

from manager.frame import Frame
from websockets.sync.client import connect as _ws_connect

from manager.serializer import serialize


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


class WebSocketTransport(IServerTransport):
    def __init__(self):
        self._conn = None

    def connect(self, url: str) -> bool:
        try:
            self._conn = _ws_connect(url, open_timeout=5)
            return True
        except Exception:
            self._conn = None
            return False

    def send(self, frame: Frame) -> bool:
        if self._conn is None:
            return False
        try:
            self._conn.send(serialize(frame))  # bytes -> binary frame
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
