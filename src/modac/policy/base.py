"""The Policy interface — this is where a model gets plugged in."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from modac.protocol import Action


class Policy(ABC):
    """Maps a frame (RGB uint8 HxWx3) to an Action.

    To use a real model: load weights in ``__init__`` and run inference in
    ``act``. Keep any recurrent / temporal state on the instance and clear it
    in ``reset`` (called at the start of each episode).
    """

    def reset(self) -> None:
        """Start of an episode. Clear recurrent state, frame stacks, etc."""

    @abstractmethod
    def act(self, frame: np.ndarray, info: dict | None = None) -> Action:
        """Return the action to take for this frame."""
        raise NotImplementedError
