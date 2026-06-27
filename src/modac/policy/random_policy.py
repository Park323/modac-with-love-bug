"""A placeholder policy so the full loop runs before a model exists."""

from __future__ import annotations

import random

import numpy as np

from modac.policy.base import Policy
from modac.protocol import Action


class RandomPolicy(Policy):
    """Wanders forward, jitters the camera, occasionally fires.

    Useful for smoke-testing the pipeline end to end. Replace with a model.
    """

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)

    def act(self, frame: np.ndarray, info: dict | None = None) -> Action:
        r = self._rng
        return Action(
            forward=r.random() < 0.6,
            left=r.random() < 0.1,
            right=r.random() < 0.1,
            jump=r.random() < 0.02,
            sprint=r.random() < 0.3,
            yaw=r.uniform(-30, 30),
            pitch=r.uniform(-5, 5),
            fire=r.random() < 0.05,
        )
