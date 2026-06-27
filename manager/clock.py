import time
from dataclasses import dataclass, field


@dataclass
class Clock:
    wall_start_ms: int = 0
    _mono_start: float = field(default=0.0, repr=False)

    def start(self) -> None:
        self.wall_start_ms = int(time.time() * 1000)
        self._mono_start = time.monotonic()

    def now_ms(self) -> int:
        elapsed_ms = int((time.monotonic() - self._mono_start) * 1000)
        return self.wall_start_ms + elapsed_ms
