"""Key name ↔ VK code mappings, scan code lookup, and polling lists."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
MAPVK_VK_TO_VSC = 0

# ── VK code constants ─────────────────────────────────────────────────────────

VK_LBUTTON  = 0x01
VK_RBUTTON  = 0x02
VK_MBUTTON  = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

_MOUSE_VKS = {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON, VK_XBUTTON1, VK_XBUTTON2}

# Extended keys that need KEYEVENTF_EXTENDEDKEY flag during replay
EXTENDED_VKS: frozenset[int] = frozenset({
    0xA3,  # VK_RCONTROL
    0xA5,  # VK_RMENU
    0x2D,  # VK_INSERT
    0x2E,  # VK_DELETE
    0x24,  # VK_HOME
    0x23,  # VK_END
    0x21,  # VK_PRIOR (Page Up)
    0x22,  # VK_NEXT  (Page Down)
    0x25,  # VK_LEFT
    0x26,  # VK_UP
    0x27,  # VK_RIGHT
    0x28,  # VK_DOWN
})

# ── name ↔ VK ─────────────────────────────────────────────────────────────────

NAME_TO_VK: dict[str, int] = {
    # movement
    "W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44,
    # actions
    "Q": 0x51, "E": 0x45, "R": 0x52, "G": 0x47, "B": 0x42,
    "F": 0x46, "T": 0x54, "V": 0x56, "X": 0x58, "Z": 0x5A,
    "C": 0x43, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B,
    "L": 0x4C, "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50,
    "U": 0x55, "Y": 0x59,
    # number row
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39, "0": 0x30,
    # modifiers / common
    "Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12,
    "Space": 0x20, "Tab": 0x09, "Esc": 0x1B, "Enter": 0x0D,
    "Backspace": 0x08,
    # function keys
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    # arrow / nav
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
    "Insert": 0x2D, "Delete": 0x2E,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
}

VK_TO_NAME: dict[int, str] = {v: k for k, v in NAME_TO_VK.items()}

# Mouse button VK → event name
MOUSE_VK_TO_NAME: dict[int, str] = {
    VK_LBUTTON: "left",
    VK_RBUTTON: "right",
    VK_MBUTTON: "middle",
}

# All keyboard VK codes to poll during recording (excludes mouse buttons)
ALL_KEYBOARD_VKS: list[int] = [
    vk for vk in range(1, 256) if vk not in _MOUSE_VKS
]


# ── scan code lookup ──────────────────────────────────────────────────────────

def scan_code_for_vk(vk: int) -> int:
    """Return the hardware scan code for a VK code (0 if unknown)."""
    scan = int(_user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    # Arrow keys need explicit scan codes when MapVirtualKeyW returns 0
    _arrow_fallback = {0x25: 0x4B, 0x26: 0x48, 0x27: 0x4D, 0x28: 0x50}
    return scan or _arrow_fallback.get(vk, 0)
