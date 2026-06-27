import numpy as np

from modac.protocol import Action
from modac.policy.random_policy import RandomPolicy


def test_random_policy_returns_action():
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    action = RandomPolicy(seed=0).act(frame)
    assert isinstance(action, Action)


def test_random_policy_is_deterministic_with_seed():
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    a = RandomPolicy(seed=42).act(frame)
    b = RandomPolicy(seed=42).act(frame)
    assert a == b
