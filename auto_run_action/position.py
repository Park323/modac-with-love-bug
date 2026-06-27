"""get_position: frame 하나를 받아 캐릭터 position을 반환하는 함수."""

from __future__ import annotations

import numpy as np

from .locator import Locator

_locator = Locator()


def get_position(frame: np.ndarray) -> dict | None:
    """
    INPUT  : frame — BGR 전체화면 캡처 (np.ndarray, 1600×900)
    OUTPUT : {"x": float, "y": float, "rot": float}
             None  ← 감지 실패 시
    """
    return _locator.locate(frame)
