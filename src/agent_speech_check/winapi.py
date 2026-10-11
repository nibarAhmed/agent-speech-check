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


# ---------------------------------------------------------------- processes

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE = wintypes.HANDLE(-1).value
MAX_PATH = 32768


class _ProcessEntry(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    )


_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def process_ids(image: str) -> list[int]:
    """Pids of running processes whose exe file name is `image` (case-insensitive)."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    pids: list[int] = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == image.lower():
                pids.append(int(entry.th32ProcessID))
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return pids


def image_path(pid: int) -> Path | None:
    """The full exe path of a process, needing only limited query access."""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(MAX_PATH)
        size = wintypes.DWORD(MAX_PATH)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value)
    finally:
        _kernel32.CloseHandle(handle)


def process_image(image: str) -> Path | None:
    for pid in process_ids(image):
        path = image_path(pid)
        if path is not None:
            return path
    return None


# ---------------------------------------------------------------- keyboard

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
_user32.VkKeyScanW.restype = ctypes.c_short


def vk_key_scan(char: str) -> tuple[int, int] | None:
    """(virtual key, shift state bits: 1 Shift, 2 Control, 4 Alt) for a character
    in the current keyboard layout, or None if no key types it."""
    result = int(_user32.VkKeyScanW(char))
    if result == -1:
        return None
    return result & 0xFF, (result >> 8) & 0xFF


# ---------------------------------------------------------------- windows

SW_MINIMIZE = 6
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


def windows_titled(prefix: str) -> list[int]:
    """Visible top-level windows whose title starts with `prefix`."""
    found: list[int] = []

    def visit(hwnd: int, _param: int) -> bool:
        if _user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(hwnd, buffer, 512)
            if buffer.value.startswith(prefix):
                found.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def minimize(hwnd: int) -> None:
    """Minimize a window; Windows activates the window that was below it."""
    _user32.ShowWindow(hwnd, SW_MINIMIZE)


_user32.IsIconic.argtypes = [wintypes.HWND]


def is_minimized(hwnd: int) -> bool:
    return bool(_user32.IsIconic(hwnd))


# ---------------------------------------------------------------- job objects

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class _IoCounters(ctypes.Structure):
    _fields_ = tuple(
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    )


class _BasicLimits(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _ExtendedLimits(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


_kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
]
_kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]


def kill_on_close_job(pid: int) -> int:
    """Put a process in a new job whose processes all die when the handle closes,
    including when this process crashes. Returns the job handle."""
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    process = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _kernel32.AssignProcessToJobObject(job, process):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel32.CloseHandle(process)
    return int(job)


def close_handle(handle: int) -> None:
    _kernel32.CloseHandle(handle)
