from manager.frame import Frame
from manager.sources import IFrameSource
from manager.transport import IServerTransport


class FakeSource(IFrameSource):
    def __init__(self, frame: Frame):
        self.frame = frame
        self.next_calls = 0

    def next(self) -> Frame:
        self.next_calls += 1
        return self.frame


class FakeTransport(IServerTransport):
    def __init__(self):
        self.connected_url = None
        self.connect_result = True
        self.sent = []
        self.closed = False
        self.on_send = None  # callable(Frame)

    def connect(self, url: str) -> bool:
        self.connected_url = url
        return self.connect_result

    def send(self, frame: Frame) -> bool:
        self.sent.append(frame)
        if self.on_send:
            self.on_send(frame)
        return True

    def close(self) -> None:
        self.closed = True
