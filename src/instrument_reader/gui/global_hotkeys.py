"""System-wide hotkeys on Windows (RegisterHotKey), so F9/F10 work while Excel has the focus.

The window that registered them gets WM_HOTKEY in ``nativeEvent``; ``hotkey_id`` turns that
message into the registered id.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000  # holding the key down does not repeat the press
VK_F9 = 0x78
VK_F10 = 0x79


def available() -> bool:
    return sys.platform == "win32"


def register(hwnd: int, hotkey_id: int, vk: int) -> bool:
    if not available():
        return False
    return bool(ctypes.windll.user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, MOD_NOREPEAT, vk))


def unregister(hwnd: int, hotkey_id: int) -> None:
    if available():
        ctypes.windll.user32.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)


def hotkey_id(event_type, message) -> int | None:
    """The hotkey id if this native event is a WM_HOTKEY, else None."""
    if not available() or bytes(event_type) != b"windows_generic_MSG":
        return None
    msg = wintypes.MSG.from_address(int(message))
    return int(msg.wParam) if msg.message == WM_HOTKEY else None
