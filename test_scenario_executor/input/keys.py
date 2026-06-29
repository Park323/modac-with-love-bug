"""Key maps — re-exported from the shared input core.

Single source of truth: ``record_replay/src/keys.py``. This shim keeps
``test_scenario_executor.input.keys`` import paths stable while removing
duplication.
"""

from record_replay.src.keys import *  # noqa: F401,F403
from record_replay.src.keys import (  # noqa: F401  explicit (underscore-safe) re-exports
    ALL_KEYBOARD_VKS,
    EXTENDED_VKS,
    MOUSE_VK_TO_NAME,
    NAME_TO_VK,
    VK_TO_NAME,
    require_windows,
    scan_code_for_vk,
)
