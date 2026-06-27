import time

from manager.sources import IFrameSource
from manager.transport import IServerTransport


class FrameStreamer:
    def __init__(self, source: IFrameSource, transport: IServerTransport, fps: int):
        self._source = source
        self._transport = transport
        self._fps = fps if fps > 0 else 1
        self._running = False

    def tick(self) -> None:
        frame = self._source.next()
        self._transport.send(frame)

    def run(self) -> None:
        self._running = True
        interval = 1.0 / self._fps
        while self._running:
            self.tick()
            if not self._running:
                break
            time.sleep(interval)

    def stop(self) -> None:
        self._running = False
