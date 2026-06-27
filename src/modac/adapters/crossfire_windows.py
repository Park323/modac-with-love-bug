"""Reference CrossFire adapter for Windows (capture + input injection).

This is the implementation the separate worker running the game can build on.
It is intentionally self-contained: capture via dxcam, input via win_input.

Notes / TODOs for the integrator:
  * `region=(left, top, right, bottom)` crops to the game window. Leave None
    for full primary monitor. Targeting a specific window (by title) is left
    to the integrator's setup.
  * `sensitivity` scales policy yaw/pitch into raw mouse counts; tune it to the
    game's in-game sensitivity so a given delta turns a predictable amount.
  * Most FPS titles need raw/relative mouse input; win_input uses SendInput
    relative motion for exactly this.
"""

from __future__ import annotations

import numpy as np

from modac.adapters.base import EnvAdapter
from modac.protocol import Action

# Maps Action boolean fields -> key names in win_input.SCAN.
_KEYMAP = {
    "forward": "w",
    "back": "s",
    "left": "a",
    "right": "d",
    "jump": "space",
    "crouch": "ctrl",
    "sprint": "shift",
    "reload": "r",
    "use": "e",
}


class CrossFireWindowsAdapter(EnvAdapter):
    def __init__(self, region: tuple[int, int, int, int] | None = None,
                 output_idx: int = 0, sensitivity: float = 1.0):
        try:
            import dxcam
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "CrossFire adapter needs Windows deps. On the game machine run:\n"
                '    pip install "modac[windows]"'
            ) from e

        from modac.adapters import win_input

        self._win = win_input
        self._cam = dxcam.create(output_idx=output_idx, output_color="RGB")
        self._region = region
        self._sensitivity = sensitivity
        self._last_frame: np.ndarray | None = None
        self._held_keys: set[str] = set()
        self._held_buttons: set[str] = set()

    def grab(self) -> np.ndarray:
        frame = self._cam.grab(region=self._region)
        if frame is None:  # dxcam returns None when no new frame is ready
            if self._last_frame is None:
                raise RuntimeError("No frame captured yet — is the game visible?")
            return self._last_frame
        self._last_frame = frame
        return frame

    def apply(self, action: Action) -> None:
        # Keyboard: diff desired vs. held, press/release only on change.
        want = {key for field, key in _KEYMAP.items() if getattr(action, field)}
        for key in want - self._held_keys:
            self._win.key_down(key)
        for key in self._held_keys - want:
            self._win.key_up(key)
        self._held_keys = want

        # Camera: relative mouse motion.
        dx = int(round(action.yaw * self._sensitivity))
        dy = int(round(action.pitch * self._sensitivity))
        if dx or dy:
            self._win.mouse_move(dx, dy)

        # Mouse buttons: diff desired vs. held.
        want_btn = set()
        if action.fire:
            want_btn.add("left")
        if action.aim:
            want_btn.add("right")
        for btn in want_btn - self._held_buttons:
            self._win.mouse_button(btn, down=True)
        for btn in self._held_buttons - want_btn:
            self._win.mouse_button(btn, down=False)
        self._held_buttons = want_btn

        # Weapon select: tap the slot key once.
        if action.weapon:
            self._win.tap(str(action.weapon))

    def close(self) -> None:
        # Release everything still held so the game doesn't get stuck moving.
        for key in self._held_keys:
            self._win.key_up(key)
        for btn in self._held_buttons:
            self._win.mouse_button(btn, down=False)
        self._held_keys.clear()
        self._held_buttons.clear()
        if self._cam is not None:
            self._cam.release()
