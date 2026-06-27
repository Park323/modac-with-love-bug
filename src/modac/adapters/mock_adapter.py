"""A cross-platform stand-in adapter — no game required.

Generates synthetic, time-varying frames and logs the actions it receives,
so the full capture -> act -> apply loop can be exercised on macOS/Linux.
"""

from __future__ import annotations

import numpy as np

from modac.adapters.base import EnvAdapter
from modac.protocol import Action


class MockAdapter(EnvAdapter):
    def __init__(self, width: int = 640, height: int = 360, log: bool = True):
        self.width = width
        self.height = height
        self.log = log
        self._t = 0

    def grab(self) -> np.ndarray:
        self._t += 1
        ramp = np.linspace(0, 255, self.width, dtype=np.uint8)
        base = np.tile(ramp, (self.height, 1))
        frame = np.stack(
            [base, np.roll(base, self._t % self.width, axis=1), 255 - base],
            axis=-1,
        ).astype(np.uint8)
        # A moving red "target" so frames are visibly distinct over time.
        cx = (self._t * 7) % self.width
        cy = self.height // 2
        frame[max(0, cy - 20) : cy + 20, max(0, cx - 20) : cx + 20] = (255, 0, 0)
        return frame

    def apply(self, action: Action) -> None:
        if not self.log:
            return
        keys = [
            k
            for k in ("forward", "back", "left", "right", "jump", "sprint", "fire", "aim")
            if getattr(action, k)
        ]
        print(
            f"[mock t={self._t:>5}] keys={keys} "
            f"look=({action.yaw:+6.1f},{action.pitch:+5.1f}) weapon={action.weapon}"
        )
