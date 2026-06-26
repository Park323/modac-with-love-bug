#!/usr/bin/env python3
"""
Record and replay keyboard/mouse scenarios for Windows game QA.

No third-party packages are required. Run with Python 3 on Windows.
Stop recording with Ctrl+Shift+F12.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time
from pathlib import Path


if os.name != "nt":
    raise SystemExit("This tool only runs on Windows.")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

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
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_F12 = 0x7B

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

LLKHF_EXTENDED = 0x01
STOP_HOTKEY_VKS = {
    VK_CONTROL,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_SHIFT,
    VK_LSHIFT,
    VK_RSHIFT,
    VK_F12,
}
CTRL_VKS = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
SHIFT_VKS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}

ULONG_PTR = wintypes.WPARAM
LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
LowLevelMouseProc = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
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
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def signed_word(value: int) -> int:
    word = (value >> 16) & 0xFFFF
    return word - 0x10000 if word & 0x8000 else word


def high_word(value: int) -> int:
    return (value >> 16) & 0xFFFF


def now() -> float:
    return time.perf_counter()


class Recorder:
    def __init__(self, output: Path, move_interval: float) -> None:
        self.output = output
        self.move_interval = move_interval
        self.events: list[dict] = []
        self.start = now()
        self.last_move_time = 0.0
        self.last_move_pos: tuple[int, int] | None = None
        self.pressed: set[int] = set()
        self.keyboard_hook = None
        self.mouse_hook = None
        self.keyboard_proc = LowLevelKeyboardProc(self._keyboard_callback)
        self.mouse_proc = LowLevelMouseProc(self._mouse_callback)

    def elapsed(self) -> float:
        return round(now() - self.start, 6)

    def run(self) -> None:
        print("Recording. Stop with Ctrl+Shift+F12.")
        self.start = now()
        module = kernel32.GetModuleHandleW(None)
        self.keyboard_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self.keyboard_proc, module, 0
        )
        self.mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self.mouse_proc, module, 0)
        if not self.keyboard_hook or not self.mouse_hook:
            raise ctypes.WinError()

        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self.keyboard_hook:
                user32.UnhookWindowsHookEx(self.keyboard_hook)
            if self.mouse_hook:
                user32.UnhookWindowsHookEx(self.mouse_hook)
            self.save()

    def stop_requested(self, vk: int) -> bool:
        return (
            vk == VK_F12
            and any(ctrl in self.pressed for ctrl in CTRL_VKS)
            and any(shift in self.pressed for shift in SHIFT_VKS)
        )

    def remove_stop_hotkey_tail(self) -> None:
        while self.events:
            event = self.events[-1]
            if event.get("kind") != "keyboard" or event.get("vk") not in STOP_HOTKEY_VKS:
                break
            self.events.pop()

    def save(self) -> None:
        self.remove_stop_hotkey_tail()
        payload = {
            "version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stop_hotkey": "Ctrl+Shift+F12",
            "events": self.events,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Saved {len(self.events)} events to {self.output}")

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(data.vkCode)
            is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)

            if is_down:
                self.pressed.add(vk)
                if self.stop_requested(vk):
                    user32.PostQuitMessage(0)
                    return 1
            elif is_up:
                self.pressed.discard(vk)

            if is_down or is_up:
                self.events.append(
                    {
                        "t": self.elapsed(),
                        "kind": "keyboard",
                        "action": "down" if is_down else "up",
                        "vk": vk,
                        "scan": int(data.scanCode),
                        "extended": bool(data.flags & LLKHF_EXTENDED),
                    }
                )
        return user32.CallNextHookEx(self.keyboard_hook, n_code, w_param, l_param)

    def _mouse_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            data = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x, y = int(data.pt.x), int(data.pt.y)
            event_time = self.elapsed()
            action = {
                WM_MOUSEMOVE: "move",
                WM_LBUTTONDOWN: "left_down",
                WM_LBUTTONUP: "left_up",
                WM_RBUTTONDOWN: "right_down",
                WM_RBUTTONUP: "right_up",
                WM_MBUTTONDOWN: "middle_down",
                WM_MBUTTONUP: "middle_up",
                WM_MOUSEWHEEL: "wheel",
                WM_MOUSEHWHEEL: "hwheel",
                WM_XBUTTONDOWN: "x_down",
                WM_XBUTTONUP: "x_up",
            }.get(w_param)

            if action == "move":
                too_soon = event_time - self.last_move_time < self.move_interval
                same_pos = self.last_move_pos == (x, y)
                if too_soon or same_pos:
                    return user32.CallNextHookEx(
                        self.mouse_hook, n_code, w_param, l_param
                    )
                self.last_move_time = event_time
                self.last_move_pos = (x, y)

            if action:
                event = {
                    "t": event_time,
                    "kind": "mouse",
                    "action": action,
                    "x": x,
                    "y": y,
                }
                if action in ("wheel", "hwheel"):
                    event["delta"] = signed_word(int(data.mouseData))
                if action in ("x_down", "x_up"):
                    event["button"] = high_word(int(data.mouseData))
                self.events.append(event)
        return user32.CallNextHookEx(self.mouse_hook, n_code, w_param, l_param)


def normalize_position(x: int, y: int) -> tuple[int, int]:
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
    height = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
    return (
        int((x - left) * 65535 / width),
        int((y - top) * 65535 / height),
    )


def send_mouse(x: int, y: int, flags: int, mouse_data: int = 0) -> None:
    nx, ny = normalize_position(x, y)
    input_data = INPUT(
        type=INPUT_MOUSE,
        union=INPUTUNION(
            mi=MOUSEINPUT(
                dx=nx,
                dy=ny,
                mouseData=mouse_data,
                dwFlags=flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    if user32.SendInput(1, ctypes.byref(input_data), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def send_keyboard(scan: int, extended: bool, is_up: bool) -> None:
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if is_up:
        flags |= KEYEVENTF_KEYUP
    input_data = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUTUNION(
            ki=KEYBDINPUT(
                wVk=0,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    if user32.SendInput(1, ctypes.byref(input_data), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def play(path: Path, speed: float, countdown: float) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    if not events:
        raise SystemExit(f"No events found in {path}")

    print(f"Replaying {len(events)} events from {path}")
    if countdown > 0:
        print(f"Starting in {countdown:g} seconds. Focus the game window now.")
        time.sleep(countdown)

    last_t = 0.0
    for event in events:
        event_t = float(event["t"])
        delay = max(0.0, (event_t - last_t) / speed)
        if delay:
            time.sleep(delay)
        last_t = event_t

        if event["kind"] == "keyboard":
            send_keyboard(
                scan=int(event["scan"]),
                extended=bool(event.get("extended", False)),
                is_up=event["action"] == "up",
            )
        elif event["kind"] == "mouse":
            action = event["action"]
            x, y = int(event["x"]), int(event["y"])
            flags = {
                "move": MOUSEEVENTF_MOVE,
                "left_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN,
                "left_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP,
                "right_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTDOWN,
                "right_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_RIGHTUP,
                "middle_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_MIDDLEDOWN,
                "middle_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_MIDDLEUP,
                "wheel": MOUSEEVENTF_MOVE | MOUSEEVENTF_WHEEL,
                "hwheel": MOUSEEVENTF_MOVE | MOUSEEVENTF_HWHEEL,
                "x_down": MOUSEEVENTF_MOVE | MOUSEEVENTF_XDOWN,
                "x_up": MOUSEEVENTF_MOVE | MOUSEEVENTF_XUP,
            }.get(action)
            if flags is None:
                continue
            mouse_data = int(event.get("delta", event.get("button", 0)))
            send_mouse(x, y, flags, mouse_data)
    print("Replay complete.")


def inspect(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events", [])
    duration = events[-1]["t"] if events else 0
    keyboard = sum(1 for event in events if event.get("kind") == "keyboard")
    mouse = sum(1 for event in events if event.get("kind") == "mouse")
    print(f"File: {path}")
    print(f"Events: {len(events)}")
    print(f"Duration: {duration:.3f}s")
    print(f"Keyboard: {keyboard}")
    print(f"Mouse: {mouse}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record and replay Windows keyboard/mouse scenarios for game QA."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    record_parser = sub.add_parser("record", help="record a scenario")
    record_parser.add_argument("output", type=Path, help="output JSON scenario path")
    record_parser.add_argument(
        "--move-interval",
        type=float,
        default=0.01,
        help="minimum seconds between stored mouse move events (default: 0.01)",
    )

    play_parser = sub.add_parser("play", help="play a scenario")
    play_parser.add_argument("input", type=Path, help="input JSON scenario path")
    play_parser.add_argument("--speed", type=float, default=1.0, help="playback speed")
    play_parser.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="seconds to wait before replay starts (default: 3)",
    )

    inspect_parser = sub.add_parser("inspect", help="show scenario summary")
    inspect_parser.add_argument("input", type=Path, help="input JSON scenario path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            Recorder(args.output, args.move_interval).run()
        elif args.command == "play":
            if args.speed <= 0:
                raise SystemExit("--speed must be greater than 0")
            play(args.input, args.speed, args.countdown)
        elif args.command == "inspect":
            inspect(args.input)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
