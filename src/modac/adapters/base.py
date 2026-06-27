"""The game-side interface. Swap implementations to retarget the system."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from modac.protocol import Action


class EnvAdapter(ABC):
    """Bridges a concrete game to the policy server.

    Implement two things:
      grab()  -> the current frame as RGB uint8 HxWx3
      apply() -> realize an Action in the game (keys / mouse)
    """

    @abstractmethod
    def grab(self) -> np.ndarray:
        ...

    @abstractmethod
    def apply(self, action: Action) -> None:
        ...

    def close(self) -> None:
        """Release capture / input handles."""
