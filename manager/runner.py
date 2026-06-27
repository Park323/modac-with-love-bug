import threading

from manager.clock import Clock
from manager.items import InputItem
from manager.modules import IPlayModule
from manager.scenario import ScenarioReader


class RunController:
    """백그라운드 스레드에서 시나리오를 repeat회 반복 재생. 동시 1개."""

    def __init__(self, play: IPlayModule, clock: Clock) -> None:
        self._play = play
        self._clock = clock
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
        if self._running:
            raise RuntimeError("already running")
        if repeat <= 0:
            repeat = 1
        events = ScenarioReader.read(path)  # 실패 시 호출자에게 전파

        with self._lock:
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
        try:
            self._clock.start()
            self._play.begin(self._clock)
            count = 0
            for r in range(repeat):
                if not self._running:
                    break
                for ev in events:
                    if not self._running:
                        break
                    item = InputItem(key=str(ev.get("type", "")),
                                     action="", raw=ev)
                    self._play.dispatch(item)
                    count += 1
                    with self._lock:
                        self._repeat_index = r + 1
                        self._item_index = count
            self._play.end()
            with self._lock:
                self._state = "done" if self._running else "stopped"
        except Exception as e:  # noqa: BLE001 - 상태로 전파
            with self._lock:
                self._state = "error"
                self._error = str(e)
        finally:
            self._running = False

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
