import threading
import time

from manager.clock import Clock
from manager.items import InputItem
from manager.modules import IPlayModule
from manager.scenario import ScenarioReader


class RunController:
    """백그라운드 스레드에서 시나리오를 repeat회 반복 재생. 동시 1개."""

    def __init__(self, play: IPlayModule, clock: Clock,
                 realtime: bool = True) -> None:
        self._play = play
        self._clock = clock
        # realtime=True: event["t"]만큼 대기하며 원본 녹화 속도로 재생.
        # False: 타이밍 무시, 즉시 dispatch(테스트용).
        self._realtime = realtime
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._state = "idle"
        self._repeat = 0
        self._repeat_index = 0
        self._item_index = 0
        self._total = 0
        self._error: str | None = None

    def start(self, path: str, repeat: int) -> None:
        if repeat <= 0:
            repeat = 1
        events = ScenarioReader.read(path)  # 락 밖: 실패 시 호출자 전파, 상태 변경 없음

        with self._lock:
            if self._running:
                raise RuntimeError("already running")
            self._running = True
            self._state = "running"
            self._repeat = repeat
            self._repeat_index = 0
            self._item_index = 0
            self._total = repeat * len(events)
            self._error = None

        self._thread = threading.Thread(
            target=self._run, args=(events, repeat), daemon=True)
        self._thread.start()

    def _run(self, events: list[dict], repeat: int) -> None:
        stopped_early = False
        try:
            self._clock.start()
            self._play.begin(self._clock)
            count = 0
            for r in range(repeat):
                if not self._running:
                    stopped_early = True
                    break
                t_start = time.perf_counter()  # 반복마다 타이밍 기준 리셋
                for ev in events:
                    if not self._running:
                        stopped_early = True
                        break
                    if self._realtime:
                        target = t_start + float(ev.get("t", 0.0) or 0.0)
                        if not self._wait_until(target):
                            stopped_early = True
                            break
                    item = InputItem(key=str(ev.get("type", "")),
                                     action="", raw=ev)
                    self._play.dispatch(item)
                    count += 1
                    with self._lock:
                        self._repeat_index = r + 1
                        self._item_index = count
                if stopped_early:
                    break
            self._play.end()
            with self._lock:
                self._state = "stopped" if stopped_early else "done"
        except Exception as e:  # noqa: BLE001 - 상태로 전파
            with self._lock:
                self._state = "error"
                self._error = str(e)
        finally:
            self._running = False

    def _wait_until(self, target: float) -> bool:
        """target(perf_counter 기준)까지 대기. 긴 간격도 stop에 즉시 반응하도록
        잘게(<=50ms) 쪼개 _running 체크. 도중 stop되면 False, 정상 도달 True."""
        while self._running:
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, 0.05))
        return False

    def stop(self) -> None:
        self._running = False

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "repeat_index": self._repeat_index,
                "repeat": self._repeat,
                "item_index": self._item_index,
                "total": self._total,
                "error": self._error,
            }
