"""Starting asc's own background processes (the daemon, the updater)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import IO

BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def clean_env(
    environ: dict[str, str] | None = None,
    *,
    platform: str = sys.platform,
    frozen: bool | None = None,
) -> dict[str, str] | None:
    """Restore pre-PyInstaller LD_LIBRARY_PATH so child processes load system libs, not asc's."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if platform == "win32" or not is_frozen:
        return None
    env = dict(os.environ if environ is None else environ)
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def asc_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "agent_speech_check", *args]


def detached(
    command: list[str], err: IO[bytes] | int, cwd: Path | None = None
) -> subprocess.Popen[bytes]:
    def popen(flags: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - our own executable and arguments
            command,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            cwd=cwd,
            close_fds=True,
        )

    if sys.platform != "win32":
        return popen(0)
    DETACHED = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        # Leave the caller's job object, so the process outlives the shell
        # that started it.
        return popen(DETACHED | BREAKAWAY)
    except PermissionError:
        # The job doesn't allow breakaway; the process then lives as long as it.
        return popen(DETACHED)
