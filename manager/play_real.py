"""실제 Play 모듈 어댑터 — test_scenario_executor.ActionPlayer를 IPlayModule로 감쌈.

RunController가 시나리오 이벤트를 한 개씩 dispatch하면, 그 이벤트(raw dict)를
ActionPlayer._dispatch로 넘겨 실제 Windows 키/마우스 입력을 발생시킨다.
타이밍은 RunController가 event["t"]로 페이싱(realtime)하므로, 여기서는
액션 1개를 즉시 실행만 한다.

주의: 진짜 OS 입력을 발생시킨다. 테스트/CI에서는 절대 사용하지 말 것
(테스트는 StubPlayModule / fake 사용).
"""

from manager.clock import Clock
from manager.items import InputItem, InputResult
from manager.modules import IPlayModule


class RealPlayModule(IPlayModule):
    def __init__(self, jitter_ms: float = 0.0) -> None:
        self._jitter_ms = jitter_ms
        self._player = None
        self._clock: Clock | None = None
        self._count = 0

    def begin(self, clock: Clock) -> None:
        # 지연 import: 이 모듈 import 시점에 win_input 강제 로드 안 함
        from test_scenario_executor.playback.player import ActionPlayer

        self._clock = clock
        self._player = ActionPlayer(jitter_ms=self._jitter_ms)
        self._count = 0

    def dispatch(self, item: InputItem) -> None:
        if self._player is None:
            raise RuntimeError("dispatch called before begin")
        action = item.raw if item.raw is not None else {}
        self._player._dispatch(action)  # 액션 1개 즉시 실행(타이밍은 RunController)
        self._count += 1

    def end(self) -> list[InputResult]:
        if self._player is not None:
            self._player.stop()
        ts = self._clock.now_ms() if self._clock is not None else 0
        # Real 모듈은 개별 결과를 돌려주지 않음 — 디스패치 수만 요약 결과로 반환.
        return [InputResult(item=InputItem(key="", action=""),
                            timestamp_ms=ts, ok=True)
                for _ in range(self._count)]
