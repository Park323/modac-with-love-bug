"""The wire contract between a game adapter and the policy server.

This module is the single source of truth for what an observation and an
action look like. Both sides import it; the separate team building the
capture/injection client only needs this file to integrate.
"""

from __future__ import annotations

import io
import time

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field


class Action(BaseModel):
    """What to do for a single frame.

    Boolean fields are "held this frame" (key/button state). Camera motion is
    a *relative* delta, matching how FPS mouselook works — the adapter feeds
    these straight into a relative mouse-move event.
    """

    # --- Movement (held this frame) ---
    forward: bool = False
    back: bool = False
    left: bool = False
    right: bool = False
    jump: bool = False
    crouch: bool = False
    sprint: bool = False

    # --- Camera (relative mouse motion, in raw mouse counts) ---
    yaw: float = 0.0  # + turns right
    pitch: float = 0.0  # + looks down

    # --- Combat / interaction ---
    fire: bool = False  # left mouse button
    aim: bool = False  # right mouse button (ADS)
    reload: bool = False
    use: bool = False  # "E" / interact

    # --- Weapon select: 0 = no change, 1..9 = slot ---
    weapon: int = Field(default=0, ge=0, le=9)

    @classmethod
    def idle(cls) -> "Action":
        return cls()


class FrameMeta(BaseModel):
    """Optional metadata sent alongside a frame (via headers / WS preamble)."""

    frame_id: int = 0
    timestamp: float = Field(default_factory=time.time)
    width: int = 0
    height: int = 0


def encode_frame(frame: np.ndarray, quality: int = 80) -> bytes:
    """RGB uint8 HxWx3 array -> JPEG bytes for the wire."""
    if frame.dtype != np.uint8:
        frame = frame.astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(frame).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def decode_frame(data: bytes) -> np.ndarray:
    """JPEG/PNG bytes -> RGB uint8 HxWx3 array."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return np.asarray(img)
