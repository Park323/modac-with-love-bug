"""Input recorder for keyboard/mouse scenarios.

Adapted from record_replay/src/recorder.py.
Key difference: _get_position() uses an injected callback (get_position_fn)
instead of capturing the screen itself.
"""

from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .keys import (
    ALL_KEYBOARD_VKS,
    EXTENDED_VKS,
    MOUSE_VK_TO_NAME,
    VK_TO_NAME,
    scan_code_for_vk,
)

user32  = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SAMPLE_HZ = 120

WH_KEYBOARD_LL = 13
WH_MOUSE_LL    = 14

WM_QUIT        = 0x0012
WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_SYSKEYDOWN  = 0x0104
WM_SYSKEYUP    = 0x0105
WM_MOUSEMOVE   = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP   = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP   = 0x0208

LLKHF_EXTENDED = 0x01

ULONG_PTR = wintypes.WPARAM
LowLevelKeyboardProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
LowLevelMouseProc    = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd",    wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam",  wintypes.WPARAM),
        ("lParam",  wintypes.LPARAM),
        ("time",    wintypes.DWORD),
        ("pt",      wintypes.POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",     wintypes.DWORD),
        ("scanCode",   wintypes.DWORD),
        ("flags",      wintypes.DWORD),
        ("time",       wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          wintypes.POINT),
        ("mouseData",   wintypes.DWORD),
        ("flags",       wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


user32.SetWindowsHookExW.argtypes  = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype   = wintypes.HHOOK
user32.CallNextHookEx.argtypes     = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype      = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype  = wintypes.BOOL
user32.GetMessageW.argtypes        = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype         = wintypes.BOOL
user32.TranslateMessage.argtypes   = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype    = wintypes.BOOL
user32.DispatchMessageW.argtypes   = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype    = wintypes.LPARAM
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype  = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype  = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes   = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype    = wintypes.HMODULE


def _is_pressed(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)

def _cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class BaseRecorder:
    backend = "base"

    def __init__(self, get_position_fn: Callable[[], dict | None] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._t0      = 0.0
        self._running = False
        self._get_position_fn = get_position_fn or (lambda: None)

    @property
    def is_recording(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._running = False

    def save(self, path: str, session_id: str = "session") -> dict:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        duration = self.events[-1]["t"] if self.events else 0.0
        data = {
            "schema_version": "0.2",
            "session": {
                "session_id":   session_id,
                "game":         "CrossFire",
                "mode":         "Team Deathmatch",
                "map":          "Transport Ship 2.0",
                "recorded_at":  datetime.now(timezone.utc).isoformat(),
                "duration_sec": round(duration, 4),
                "event_count":  len(self.events),
            },
            "environment": {"backend": self.backend},
            "events": self.events,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 4)

    def _get_position(self) -> dict | None:
        return self._get_position_fn()


class HookRecorder(BaseRecorder):
    backend = "hook"

    def __init__(self, sample_hz: float = SAMPLE_HZ,
                 get_position_fn: Callable[[], dict | None] | None = None) -> None:
        super().__init__(get_position_fn)
        self._move_interval  = 1.0 / sample_hz
        self._last_move_t    = 0.0
        self._prev_cursor: tuple[int, int] | None = None
        self._thread_id      = 0
        self._keyboard_hook  = None
        self._mouse_hook     = None
        self._keyboard_proc  = LowLevelKeyboardProc(self._keyboard_callback)
        self._mouse_proc     = LowLevelMouseProc(self._mouse_callback)

    def start(self) -> None:
        self.events       = []
        self._prev_cursor = _cursor_pos()
        self._t0          = time.perf_counter()
        self._last_move_t = 0.0
        self._running     = True
        self._thread_id   = int(kernel32.GetCurrentThreadId())

        module = kernel32.GetModuleHandleW(None)
        self._keyboard_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._keyboard_proc, module, 0)
        if not self._keyboard_hook:
            self._running = False
            raise ctypes.WinError()
        self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, module, 0)
        if not self._mouse_hook:
            user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._keyboard_hook = None
            self._running = False
            raise ctypes.WinError()

        msg = MSG()
        try:
            while self._running:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
                self._keyboard_hook = None
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
                self._mouse_hook = None
            self._running  = False
            self._thread_id = 0

    def stop(self) -> None:
        super().stop()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and self._running:
            data    = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up   = w_param in (WM_KEYUP,   WM_SYSKEYUP)
            if is_down or is_up:
                vk   = int(data.vkCode)
                scan = int(data.scanCode) or scan_code_for_vk(vk)
                if scan:
                    self.events.append({
                        "t":        self._elapsed(),
                        "type":     "key_down" if is_down else "key_up",
                        "key":      VK_TO_NAME.get(vk, f"0x{vk:02X}"),
                        "scan":     scan,
                        "extended": bool(data.flags & LLKHF_EXTENDED) or vk in EXTENDED_VKS,
                        "position": self._get_position(),
                    })
        return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)

    def _mouse_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and self._running:
            data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            cur  = (int(data.pt.x), int(data.pt.y))

            if w_param == WM_MOUSEMOVE:
                event_time = self._elapsed()
                if self._prev_cursor is not None:
                    dx = cur[0] - self._prev_cursor[0]
                    dy = cur[1] - self._prev_cursor[1]
                    if (dx or dy) and event_time - self._last_move_t >= self._move_interval:
                        self._last_move_t = event_time
                        self.events.append({
                            "t":        event_time,
                            "type":     "mouse_move",
                            "dx":       dx,
                            "dy":       dy,
                            "position": self._get_position(),
                        })
                self._prev_cursor = cur
                return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)

            button_event = {
                WM_LBUTTONDOWN: ("mouse_button_down", "left"),
                WM_LBUTTONUP:   ("mouse_button_up",   "left"),
                WM_RBUTTONDOWN: ("mouse_button_down", "right"),
                WM_RBUTTONUP:   ("mouse_button_up",   "right"),
                WM_MBUTTONDOWN: ("mouse_button_down", "middle"),
                WM_MBUTTONUP:   ("mouse_button_up",   "middle"),
            }.get(w_param)
            if button_event:
                etype, button = button_event
                self.events.append({
                    "t":        self._elapsed(),
                    "type":     etype,
                    "button":   button,
                    "position": self._get_position(),
                })
            self._prev_cursor = cur
        return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)


class PollingRecorder(BaseRecorder):
    backend = "polling"

    def __init__(self, sample_hz: float = SAMPLE_HZ,
                 get_position_fn: Callable[[], dict | None] | None = None) -> None:
        super().__init__(get_position_fn)
        self._interval     = 1.0 / sample_hz
        self._prev_keys:    dict[int, bool] = {}
        self._prev_buttons: dict[int, bool] = {}
        self._prev_cursor: tuple[int, int] | None = None

    def start(self) -> None:
        self.events        = []
        self._prev_keys    = {}
        self._prev_buttons = {}
        self._prev_cursor  = _cursor_pos()
        self._t0           = time.perf_counter()
        self._running      = True
        while self._running:
            self._poll_keys()
            self._poll_mouse_buttons()
            self._poll_cursor()
            time.sleep(self._interval)

    def _poll_keys(self) -> None:
        t = self._elapsed()
        for vk in ALL_KEYBOARD_VKS:
            pressed = _is_pressed(vk)
            if pressed == self._prev_keys.get(vk, False):
                continue
            self._prev_keys[vk] = pressed
            scan = scan_code_for_vk(vk)
            if scan == 0:
                continue
            self.events.append({
                "t":        t,
                "type":     "key_down" if pressed else "key_up",
                "key":      VK_TO_NAME.get(vk, f"0x{vk:02X}"),
                "scan":     scan,
                "extended": vk in EXTENDED_VKS,
                "position": self._get_position(),
            })

    def _poll_mouse_buttons(self) -> None:
        t = self._elapsed()
        for vk, name in MOUSE_VK_TO_NAME.items():
            pressed = _is_pressed(vk)
            if pressed == self._prev_buttons.get(vk, False):
                continue
            self._prev_buttons[vk] = pressed
            self.events.append({
                "t":      t,
                "type":   "mouse_button_down" if pressed else "mouse_button_up",
                "button": name,
                "position": self._get_position(),
            })

    def _poll_cursor(self) -> None:
        cur = _cursor_pos()
        if self._prev_cursor is not None:
            dx = cur[0] - self._prev_cursor[0]
            dy = cur[1] - self._prev_cursor[1]
            if dx or dy:
                self.events.append({
                    "t":        self._elapsed(),
                    "type":     "mouse_move",
                    "dx":       dx,
                    "dy":       dy,
                    "position": self._get_position(),
                })
        self._prev_cursor = cur
