"""VK code ↔ key-name mapping for GetAsyncKeyState polling and SendInput replay."""

# key name → Windows Virtual Key code
NAME_TO_VK: dict[str, int] = {
    "W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44,
    "Q": 0x51, "E": 0x45, "R": 0x52, "G": 0x47, "B": 0x42,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "Ctrl": 0x11, "Shift": 0x10, "Space": 0x20, "Tab": 0x09,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Esc": 0x1B, "Enter": 0x0D,
}

VK_TO_NAME: dict[int, str] = {v: k for k, v in NAME_TO_VK.items()}

# VK codes that are polled each frame during recording
GAME_KEY_VKS: list[int] = [
    NAME_TO_VK[k] for k in
    ["W", "A", "S", "D", "Q", "E", "R", "G", "B",
     "1", "2", "3", "4", "5", "Ctrl", "Shift", "Space"]
]

# Mouse button VK codes
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04

MOUSE_VK_TO_NAME: dict[int, str] = {
    VK_LBUTTON: "left",
    VK_RBUTTON: "right",
    VK_MBUTTON: "middle",
}
