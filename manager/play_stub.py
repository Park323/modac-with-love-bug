from manager.clock import Clock
from manager.items import InputItem, InputResult
from manager.modules import IPlayModule


class StubPlayModule(IPlayModule):
    """이번 사이클용 Stub. dispatch 받은 아이템 기록만. 실제 연동=로드맵."""

    def __init__(self) -> None:
        self._clock: Clock | None = None
        self._dispatched: list[InputItem] = []

    def begin(self, clock: Clock) -> None:
        self._clock = clock
        self._dispatched = []

    def dispatch(self, item: InputItem) -> None:
        if self._clock is None:
            raise RuntimeError("dispatch called before begin")
        self._dispatched.append(item)

    def end(self) -> list[InputResult]:
        clock = self._clock
        ts = clock.now_ms() if clock is not None else 0
        return [InputResult(item=item, timestamp_ms=ts, ok=True)
                for item in self._dispatched]
