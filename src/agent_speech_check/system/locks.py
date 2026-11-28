"""Locks held until this process exits, so a crash can't leave one behind."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from agent_speech_check.system import winapi

if sys.platform != "win32":
    import fcntl

type Lock = int | IO[bytes]


def take(path: Path) -> Lock | None:
    """Take the lock `path` names: a named mutex on Windows, a lock on the file elsewhere.
    None if another process holds it."""
    if sys.platform == "win32":
        return winapi.take_mutex(winapi.mutex_name(path))
    else:  # not after the return: mypy on Windows would check fcntl
        lock = path.open("ab")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        return lock
