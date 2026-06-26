from __future__ import annotations

from pynput import keyboard


SPECIAL_KEYS = {
    keyboard.Key.alt: "Alt",
    keyboard.Key.alt_l: "Alt",
    keyboard.Key.alt_r: "Alt",
    keyboard.Key.backspace: "Backspace",
    keyboard.Key.caps_lock: "CapsLock",
    keyboard.Key.cmd: "Win",
    keyboard.Key.ctrl: "Ctrl",
    keyboard.Key.ctrl_l: "Ctrl",
    keyboard.Key.ctrl_r: "Ctrl",
    keyboard.Key.delete: "Delete",
    keyboard.Key.down: "Down",
    keyboard.Key.end: "End",
    keyboard.Key.enter: "Enter",
    keyboard.Key.esc: "Esc",
    keyboard.Key.f1: "F1",
    keyboard.Key.f2: "F2",
    keyboard.Key.f3: "F3",
    keyboard.Key.f4: "F4",
    keyboard.Key.f5: "F5",
    keyboard.Key.f6: "F6",
    keyboard.Key.f7: "F7",
    keyboard.Key.f8: "F8",
    keyboard.Key.f9: "F9",
    keyboard.Key.f10: "F10",
    keyboard.Key.f11: "F11",
    keyboard.Key.f12: "F12",
    keyboard.Key.home: "Home",
    keyboard.Key.insert: "Insert",
    keyboard.Key.left: "Left",
    keyboard.Key.page_down: "PageDown",
    keyboard.Key.page_up: "PageUp",
    keyboard.Key.right: "Right",
    keyboard.Key.shift: "Shift",
    keyboard.Key.shift_l: "Shift",
    keyboard.Key.shift_r: "Shift",
    keyboard.Key.space: "Space",
    keyboard.Key.tab: "Tab",
    keyboard.Key.up: "Up",
}


PYAUTOGUI_KEYS = {
    "Alt": "alt",
    "Backspace": "backspace",
    "CapsLock": "capslock",
    "Ctrl": "ctrl",
    "Delete": "delete",
    "Down": "down",
    "End": "end",
    "Enter": "enter",
    "Esc": "esc",
    "F1": "f1",
    "F2": "f2",
    "F3": "f3",
    "F4": "f4",
    "F5": "f5",
    "F6": "f6",
    "F7": "f7",
    "F8": "f8",
    "F9": "f9",
    "F10": "f10",
    "F11": "f11",
    "F12": "f12",
    "Home": "home",
    "Insert": "insert",
    "Left": "left",
    "PageDown": "pagedown",
    "PageUp": "pageup",
    "Right": "right",
    "Shift": "shift",
    "Space": "space",
    "Tab": "tab",
    "Up": "up",
    "Win": "win",
}


def normalize_key(key: keyboard.Key | keyboard.KeyCode) -> str:
    if key in SPECIAL_KEYS:
        return SPECIAL_KEYS[key]
    if isinstance(key, keyboard.KeyCode) and key.char:
        return key.char.upper() if len(key.char) == 1 else key.char
    return str(key)


def to_pyautogui_key(key: str) -> str:
    if key in PYAUTOGUI_KEYS:
        return PYAUTOGUI_KEYS[key]
    if len(key) == 1:
        return key.lower()
    return key.lower()
