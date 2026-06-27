from manager.clock import Clock
from manager.frame import Frame
from manager.modules import ICaptureModule


class StubCaptureModule(ICaptureModule):
    """테스트용 Capture stub — 실제 화면 캡처 없음. next()는 빈 Frame(bgr None)."""

    def begin(self, clock: Clock) -> None:
        pass

    def next(self) -> Frame:
        return Frame()

    def end(self) -> None:
        pass
