"""Starting and stopping a target's daemon. No printing: `cli.py` shows the results."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_speech_check import ipc, nvda, state
from agent_speech_check.daemon import LOG_FILE
from agent_speech_check.errors import (
    AlreadyRunning,
    DaemonStartFailed,
    ModeNotBuilt,
    NotConnected,
)
from agent_speech_check.targets import Target

START_TIMEOUT_S = 15.0
STOP_TIMEOUT_S = 10.0
POLL_S = 0.1
ERR_FILE = "daemon.err"
TAIL_LINES = 10
STATUS_TIMEOUT_S = 5.0

_DETACHED = (
    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined,unused-ignore]
)
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def status(folder: Path, target: str) -> dict[str, Any]:
    return ipc.call(folder, target, ipc.Request("status", ipc.NoArgs()), STATUS_TIMEOUT_S)


def daemon_command(target: Target) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "_daemon", target.to_json()]
    return [sys.executable, "-m", "agent_speech_check", "_daemon", target.to_json()]


def log_tail(folder: Path) -> str:
    lines: list[str] = []
    for name in (LOG_FILE, ERR_FILE):
        path = folder / name
        if path.exists():
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    return "\n".join(lines)


def _spawn(target: Target, folder: Path) -> subprocess.Popen[bytes]:
    with (folder / ERR_FILE).open("wb") as err:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": err,
            "cwd": folder,
            "close_fds": True,
        }
        try:
            # Leave the caller's job object, so the daemon outlives the shell
            # that started it.
            return subprocess.Popen(  # noqa: S603 - our own executable and arguments
                daemon_command(target), creationflags=_DETACHED | _BREAKAWAY, **kwargs
            )
        except PermissionError:
            # The job doesn't allow breakaway; the daemon then lives as long as it.
            return subprocess.Popen(  # noqa: S603 - our own executable and arguments
                daemon_command(target), creationflags=_DETACHED, **kwargs
            )


def start(target: Target, folder: Path) -> dict[str, Any]:
    """Start the daemon and return its first status."""
    if target.mode in ("sandbox", "shared"):
        raise ModeNotBuilt(target.mode)
    if target.mode == "desktop" and target.launch:
        # The daemon checks again; checking here gives the right exit code.
        nvda.require_exe(nvda.folder_for(target.nvda_dir))
        nvda.refuse_if_running()
    folder.mkdir(parents=True, exist_ok=True)
    if state.exists(folder):
        try:
            status(folder, target.name)
        except NotConnected:
            state.remove(folder)
        else:
            raise AlreadyRunning(target.name)
    process = _spawn(target, folder)
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise DaemonStartFailed(code, START_TIMEOUT_S, log_tail(folder))
        if state.exists(folder):
            return status(folder, target.name)
        time.sleep(POLL_S)
    raise DaemonStartFailed(None, START_TIMEOUT_S, log_tail(folder))


def stop(folder: Path, target: str) -> None:
    ipc.call(folder, target, ipc.Request("stop", ipc.NoArgs()), STATUS_TIMEOUT_S)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while state.exists(folder) and time.monotonic() < deadline:
        time.sleep(POLL_S)
