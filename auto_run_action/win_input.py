"""Low-level input injection — re-exported from the shared input core.

Single source of truth: ``record_replay/src/win_input.py``. This shim keeps
``auto_run_action.win_input`` import paths stable while removing duplication.
"""

from record_replay.src.win_input import *  # noqa: F401,F403
from record_replay.src.win_input import (  # noqa: F401  explicit re-exports
    send_keyboard_scan,
    send_keyboard_vk,
    send_mouse_absolute,
    send_mouse_button,
    send_mouse_relative,
    move_cursor_to_center,
    screen_center,
)
