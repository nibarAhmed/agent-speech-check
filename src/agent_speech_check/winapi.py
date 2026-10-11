"""Thin ctypes wrappers for the few Win32 calls `asc` needs. Keep logic elsewhere."""

from __future__ import annotations

import ctypes
import hashlib
import threading
from collections.abc import Callable
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


# ---------------------------------------------------------------- file version

_version = ctypes.WinDLL("version", use_last_error=True)
_version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
_version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
_version.GetFileVersionInfoW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
]
_version.VerQueryValueW.argtypes = [
    wintypes.LPCVOID,
    wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.LPVOID),
    wintypes.PUINT,
]


class _FixedFileInfo(ctypes.Structure):
    _fields_ = (
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
    )


def file_version(path: Path) -> tuple[int, int] | None:
    """The first two numbers of a file's version resource, or None if it has none."""
    size = _version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not _version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    pointer = wintypes.LPVOID()
    length = wintypes.UINT()
    if not _version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    if length.value < ctypes.sizeof(_FixedFileInfo):
        return None
    info = ctypes.cast(pointer, ctypes.POINTER(_FixedFileInfo)).contents
    return info.dwFileVersionMS >> 16, info.dwFileVersionMS & 0xFFFF


# ---------------------------------------------------------------- global hotkey

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
_HOTKEY_ID = 1

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class GlobalHotkey:
    """A system-wide hotkey on its own thread with a message loop.

    `start` returns False if another program already owns the combination.
    `on_press` runs on the hotkey thread.
    """

    def __init__(self, modifiers: int, vk: int, on_press: Callable[[], None]) -> None:
        self._modifiers = modifiers | MOD_NOREPEAT
        self._vk = vk
        self._on_press = on_press
        self._thread_id = 0
        self._registered = threading.Event()
        self._ok = False
        self._thread = threading.Thread(target=self._loop, name="panic-hotkey", daemon=True)

    def start(self) -> bool:
        self._thread.start()
        self._registered.wait(5)
        return self._ok

    def _loop(self) -> None:
        self._thread_id = int(_kernel32.GetCurrentThreadId())
        self._ok = bool(_user32.RegisterHotKey(None, _HOTKEY_ID, self._modifiers, self._vk))
        self._registered.set()
        if not self._ok:
            return
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    self._on_press()
        finally:
            _user32.UnregisterHotKey(None, _HOTKEY_ID)

    def post(self, message: int) -> None:
        """Post a message to the hotkey thread (WM_QUIT stops it)."""
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, message, 0, 0)

    def stop(self) -> None:
        self.post(WM_QUIT)
        self._thread.join(5)
