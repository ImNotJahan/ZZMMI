"""Tell whether ZZZ has a window on screen yet (macOS).

The game's process starts well before its window shows up (Wine start-up, anti-cheat), so the
GUI watches for the window itself. Each Wine process is its own macOS app, so the game's window
is owned by the process running ZenlessZoneZero.exe.

Uses CGWindowListCopyWindowInfo through ctypes. Window owners and bounds don't need the Screen
Recording permission (only window titles do, and those aren't read here).
"""
from __future__ import annotations

import ctypes
import re
import subprocess

from .runtime.yaagl import GAME_EXE

# Smaller windows are ignored: Wine and Unity can put up tiny helper windows before the real one.
MIN_WIDTH, MIN_HEIGHT = 320, 200

_ON_SCREEN_ONLY = 1 << 0
_EXCLUDE_DESKTOP_ELEMENTS = 1 << 4
_CF_NUMBER_SINT64 = 4


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                ("width", ctypes.c_double), ("height", ctypes.c_double)]


_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

_cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
_cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
_cg.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
_cg.CGRectMakeWithDictionaryRepresentation.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CGRect)]
_cf.CFArrayGetCount.restype = ctypes.c_long
_cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
_cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
_cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
_cf.CFDictionaryGetValue.restype = ctypes.c_void_p
_cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_cf.CFNumberGetValue.restype = ctypes.c_bool
_cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
_cf.CFRelease.argtypes = [ctypes.c_void_p]

_OWNER_PID = ctypes.c_void_p.in_dll(_cg, "kCGWindowOwnerPID").value
_LAYER = ctypes.c_void_p.in_dll(_cg, "kCGWindowLayer").value
_BOUNDS = ctypes.c_void_p.in_dll(_cg, "kCGWindowBounds").value


def _number(dictionary: int, key: int) -> int | None:
    ref = _cf.CFDictionaryGetValue(dictionary, key)
    value = ctypes.c_int64()
    if ref and _cf.CFNumberGetValue(ref, _CF_NUMBER_SINT64, ctypes.byref(value)):
        return value.value
    return None


def window_owner_pids(min_width: int = MIN_WIDTH, min_height: int = MIN_HEIGHT) -> set[int]:
    """PIDs of processes with a normal on-screen window at least this big."""
    windows = _cg.CGWindowListCopyWindowInfo(_ON_SCREEN_ONLY | _EXCLUDE_DESKTOP_ELEMENTS, 0)
    if not windows:
        return set()
    pids = set()
    try:
        for i in range(_cf.CFArrayGetCount(windows)):
            info = _cf.CFArrayGetValueAtIndex(windows, i)
            if _number(info, _LAYER) != 0:  # menus, the Dock, overlays, ...
                continue
            bounds_ref = _cf.CFDictionaryGetValue(info, _BOUNDS)
            bounds = _CGRect()
            if not (bounds_ref and _cg.CGRectMakeWithDictionaryRepresentation(bounds_ref, ctypes.byref(bounds))):
                continue
            pid = _number(info, _OWNER_PID)
            if pid is not None and bounds.width >= min_width and bounds.height >= min_height:
                pids.add(pid)
    finally:
        _cf.CFRelease(windows)
    return pids


def game_pids() -> set[int]:
    out = subprocess.run(["pgrep", "-f", re.escape(GAME_EXE)], capture_output=True, text=True).stdout
    return {int(pid) for pid in out.split()}


def game_window_visible() -> bool:
    pids = game_pids()
    return bool(pids) and bool(pids & window_owner_pids())
