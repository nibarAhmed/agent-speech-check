"""Starting asc's own background processes (the daemon, the sandbox guard)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import IO

DETACHED = (
    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined,unused-ignore]
)
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def asc_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "agent_speech_check", *args]


def detached(
    command: list[str], err: IO[bytes], cwd: Path | None = None
) -> subprocess.Popen[bytes]:
    def popen(flags: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - our own executable and arguments
            command,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            cwd=cwd,
            close_fds=True,
        )

    try:
        # Leave the caller's job object, so the process outlives the shell
        # that started it.
        return popen(DETACHED | BREAKAWAY)
    except PermissionError:
        # The job doesn't allow breakaway; the process then lives as long as it.
        return popen(DETACHED)
