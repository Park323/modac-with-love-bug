"""Reference: how a *different* game plugs into the same policy server.

This is illustrative pseudo-code (MineRL is not a dependency). It shows that
retargeting the system to another game means writing one EnvAdapter — nothing
in the policy server or the protocol changes.

    import gym, minerl  # noqa
    env = gym.make("MineRLBasaltFindCave-v0")
"""

from __future__ import annotations

import numpy as np

from modac.adapters.base import EnvAdapter
from modac.protocol import Action


class MineRLAdapter(EnvAdapter):  # pragma: no cover - reference only
    def __init__(self, env):
        self._env = env
        self._obs = env.reset()

    def grab(self) -> np.ndarray:
        # MineRL puts the frame in obs["pov"] as RGB uint8 HxWx3.
        return self._obs["pov"]

    def apply(self, action: Action) -> None:
        # Translate our Action into the env's action dict, then step.
        env_action = self._env.action_space.noop()
        env_action["forward"] = int(action.forward)
        env_action["jump"] = int(action.jump)
        env_action["attack"] = int(action.fire)
        env_action["camera"] = [action.pitch, action.yaw]
        self._obs, _reward, _done, _info = self._env.step(env_action)

    def close(self) -> None:
        self._env.close()
