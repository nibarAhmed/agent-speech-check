"""Thin ctypes wrappers for the few Win32 calls `asc` needs. Keep logic elsewhere."""

from __future__ import annotations

import ctypes
import hashlib
from ctypes import wintypes
from pathlib import Path

ERROR_ALREADY_EXISTS = 183

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE


def mutex_name(root: Path, target: str) -> str:
    """Unique per state folder and target, so test runs never meet a real daemon."""
    digest = hashlib.sha256(str(root).lower().encode()).hexdigest()[:16]
    return f"Local\\agent-speech-check-{digest}-{target}"


def take_mutex(name: str) -> int | None:
    """Create the named mutex. None if another process already holds one by that name.

    The handle is never closed on purpose: Windows releases it when the process exits.
    """
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return int(handle)
