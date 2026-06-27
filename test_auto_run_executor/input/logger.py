"""Record live keyboard and mouse input for later replay."""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .keys import (
    ALL_KEYBOARD_VKS,
    EXTENDED_VKS,
    MOUSE_VK_TO_NAME,
    VK_TO_NAME,
    require_windows,
    scan_code_for_vk,
)

SAMPLE_HZ = 120

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208

LLKHF_EXTENDED = 0x01

ULONG_PTR = wintypes.WPARAM
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
LowLevelKeyboardProc = _WINFUNCTYPE(
    wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
LowLevelMouseProc = _WINFUNCTYPE(
    wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HookInstallError(RuntimeError):
    """Raised when Windows refuses to install a low-level input hook."""


def _user32():
    require_windows()
    return ctypes.windll.user32


def _kernel32():
    require_windows()
    return ctypes.windll.kernel32


def _is_pressed(vk: int) -> bool:
    return bool(_user32().GetAsyncKeyState(vk) & 0x8000)


def _cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32().GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _format_win_error(code: int) -> str:
    if hasattr(ctypes, "FormatError"):
        return ctypes.FormatError(code)
    return f"Windows error {code}"


class InputRecorder:
    backend = "base"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._t0 = 0.0
        self._running = False

    @property
    def is_recording(self) -> bool:
        return self._running

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        self._running = False

    def save(self, path: str | Path, session_id: str = "session") -> dict[str, Any]:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        duration = self.events[-1]["t"] if self.events else 0.0
        data: dict[str, Any] = {
            "schema_version": "1.0",
            "session": {
                "session_id": session_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "duration_sec": round(duration, 4),
                "event_count": len(self.events),
            },
            "environment": {
                "module": "test_auto_run_executor",
                "backend": self.backend,
            },
            "events": self.events,
        }
        with output.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data

    def _elapsed(self) -> float:
        return round(time.perf_counter() - self._t0, 4)


class HookInputRecorder(InputRecorder):
    backend = "hook"

    def __init__(self, sample_hz: float = SAMPLE_HZ) -> None:
        super().__init__()
        self._move_interval = 1.0 / sample_hz
        self._last_move_t = 0.0
        self._prev_cursor: tuple[int, int] | None = None
        self._thread_id = 0
        self._keyboard_hook = None
        self._mouse_hook = None
        self._keyboard_proc = LowLevelKeyboardProc(self._keyboard_callback)
        self._mouse_proc = LowLevelMouseProc(self._mouse_callback)

    def start(self) -> None:
        require_windows()
        user32 = _user32()
        kernel32 = _kernel32()
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.restype = ctypes.c_long
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.SetLastError.argtypes = [wintypes.DWORD]
        kernel32.SetLastError.restype = None

        self.events = []
        self._prev_cursor = _cursor_pos()
        self._t0 = time.perf_counter()
        self._last_move_t = 0.0
        self._running = False
        self._thread_id = int(kernel32.GetCurrentThreadId())

        try:
            self._keyboard_hook = self._install_hook(
                user32, kernel32, WH_KEYBOARD_LL, self._keyboard_proc, "keyboard"
            )
            self._mouse_hook = self._install_hook(
                user32, kernel32, WH_MOUSE_LL, self._mouse_proc, "mouse"
            )
        except Exception:
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
                self._keyboard_hook = None
            self._running = False
            self._thread_id = 0
            raise

        msg = MSG()
        try:
            self._running = True
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
            self._running = False
            self._thread_id = 0

    def stop(self) -> None:
        super().stop()
        if self._thread_id:
            _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _install_hook(
        self,
        user32: Any,
        kernel32: Any,
        hook_id: int,
        proc: Any,
        label: str,
    ) -> Any:
        proc_ptr = ctypes.cast(proc, ctypes.c_void_p)
        module = kernel32.GetModuleHandleW(None)
        errors: list[str] = []

        for module_handle in (module, None):
            kernel32.SetLastError(0)
            hook = user32.SetWindowsHookExW(hook_id, proc_ptr, module_handle, 0)
            if hook:
                return hook
            code = int(kernel32.GetLastError())
            handle_name = "current module" if module_handle else "NULL module"
            errors.append(f"{handle_name}: {code} ({_format_win_error(code)})")

        self._running = False
        raise HookInstallError(
            f"Could not install {label} hook. "
            f"Tried current module and NULL module. Details: {'; '.join(errors)}. "
            "If this PC blocks global hooks, start with backend='polling'."
        )

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        user32 = _user32()
        if n_code >= 0 and self._running:
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
            if is_down or is_up:
                vk = int(data.vkCode)
                scan = int(data.scanCode) or scan_code_for_vk(vk)
                if scan:
                    self.events.append({
                        "t": self._elapsed(),
                        "type": "key_down" if is_down else "key_up",
                        "key": VK_TO_NAME.get(vk, f"0x{vk:02X}"),
                        "scan": scan,
                        "extended": bool(data.flags & LLKHF_EXTENDED) or vk in EXTENDED_VKS,
                    })
        return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)

    def _mouse_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        user32 = _user32()
        if n_code >= 0 and self._running:
            data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            cur = (int(data.pt.x), int(data.pt.y))

            if w_param == WM_MOUSEMOVE:
                event_time = self._elapsed()
                if self._prev_cursor is not None:
                    dx = cur[0] - self._prev_cursor[0]
                    dy = cur[1] - self._prev_cursor[1]
                    move_due = event_time - self._last_move_t >= self._move_interval
                    if (dx or dy) and move_due:
                        self._last_move_t = event_time
                        self.events.append({
                            "t": event_time,
                            "type": "mouse_move",
                            "dx": dx,
                            "dy": dy,
                        })
                self._prev_cursor = cur
                return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)

            button_event = {
                WM_LBUTTONDOWN: ("mouse_button_down", "left"),
                WM_LBUTTONUP: ("mouse_button_up", "left"),
                WM_RBUTTONDOWN: ("mouse_button_down", "right"),
                WM_RBUTTONUP: ("mouse_button_up", "right"),
                WM_MBUTTONDOWN: ("mouse_button_down", "middle"),
                WM_MBUTTONUP: ("mouse_button_up", "middle"),
            }.get(w_param)

            if button_event:
                event_type, button = button_event
                self.events.append({
                    "t": self._elapsed(),
                    "type": event_type,
                    "button": button,
                })
            self._prev_cursor = cur

        return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)


class PollingInputRecorder(InputRecorder):
    backend = "polling"

    def __init__(self, sample_hz: float = SAMPLE_HZ) -> None:
        super().__init__()
        self._interval = 1.0 / sample_hz
        self._prev_keys: dict[int, bool] = {}
        self._prev_buttons: dict[int, bool] = {}
        self._prev_cursor: tuple[int, int] | None = None

    def start(self) -> None:
        require_windows()
        self.events = []
        self._prev_keys = {}
        self._prev_buttons = {}
        self._prev_cursor = _cursor_pos()
        self._t0 = time.perf_counter()
        self._running = True

        while self._running:
            self._poll_keys()
            self._poll_mouse_buttons()
            self._poll_cursor()
            time.sleep(self._interval)

    def _poll_keys(self) -> None:
        t = self._elapsed()
        for vk in ALL_KEYBOARD_VKS:
            pressed = _is_pressed(vk)
            was = self._prev_keys.get(vk, False)
            if pressed == was:
                continue
            self._prev_keys[vk] = pressed
            scan = scan_code_for_vk(vk)
            if not scan:
                continue
            self.events.append({
                "t": t,
                "type": "key_down" if pressed else "key_up",
                "key": VK_TO_NAME.get(vk, f"0x{vk:02X}"),
                "scan": scan,
                "extended": vk in EXTENDED_VKS,
            })

    def _poll_mouse_buttons(self) -> None:
        t = self._elapsed()
        for vk, name in MOUSE_VK_TO_NAME.items():
            pressed = _is_pressed(vk)
            was = self._prev_buttons.get(vk, False)
            if pressed == was:
                continue
            self._prev_buttons[vk] = pressed
            self.events.append({
                "t": t,
                "type": "mouse_button_down" if pressed else "mouse_button_up",
                "button": name,
            })

    def _poll_cursor(self) -> None:
        cur = _cursor_pos()
        if self._prev_cursor is not None:
            dx = cur[0] - self._prev_cursor[0]
            dy = cur[1] - self._prev_cursor[1]
            if dx or dy:
                self.events.append({
                    "t": self._elapsed(),
                    "type": "mouse_move",
                    "dx": dx,
                    "dy": dy,
                })
        self._prev_cursor = cur


def create_input_recorder(
    backend: str = "hook", sample_hz: float = SAMPLE_HZ
) -> InputRecorder:
    if backend == "hook":
        return HookInputRecorder(sample_hz=sample_hz)
    if backend == "polling":
        return PollingInputRecorder(sample_hz=sample_hz)
    raise ValueError(f"Unknown input recorder backend: {backend}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record Windows keyboard/mouse input.")
    parser.add_argument("output", help="path to write the recording JSON")
    parser.add_argument("--session-id", default="session")
    parser.add_argument("--backend", choices=["hook", "polling"], default="hook")
    parser.add_argument("--sample-hz", type=float, default=SAMPLE_HZ)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    recorder = create_input_recorder(args.backend, sample_hz=args.sample_hz)
    print(f"Recording input with {args.backend}. Press Ctrl+C to stop.")
    try:
        recorder.start()
    except KeyboardInterrupt:
        recorder.stop()
    finally:
        result = recorder.save(args.output, args.session_id)
        print(
            f"Saved {result['session']['event_count']} events "
            f"({result['session']['duration_sec']}s) to {args.output}"
        )


if __name__ == "__main__":
    main()
