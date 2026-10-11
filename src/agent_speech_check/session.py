"""Starting and stopping a target's daemon. No printing: `cli.py` shows the results."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_speech_check import firewall, ipc, nvda, sandbox, state
from agent_speech_check.daemon import LOG_FILE
from agent_speech_check.errors import (
    AlreadyRunning,
    DaemonStartFailed,
    FirewallRuleMissing,
    ModeNotBuilt,
    NotConnected,
    NvdaDidNotConnect,
    SandboxBusy,
    SandboxFailed,
)
from agent_speech_check.targets import Target

START_TIMEOUT_S = 15.0
STOP_TIMEOUT_S = 10.0
POLL_S = 0.1
ERR_FILE = "daemon.err"
TAIL_LINES = 10
STATUS_TIMEOUT_S = 5.0
# A sandbox boots and makes its portable NVDA first, which takes a while.
CONNECT_TIMEOUT_S = {"desktop": 60.0, "sandbox": 300.0}
CONNECT_POLL_S = 1.0

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


def _check_sandbox(target: Target) -> None:
    sandbox.require_available()
    if not firewall.rule_ok(target.port, firewall.daemon_program()):
        raise FirewallRuleMissing
    if sandbox.running():
        raise SandboxBusy
    # Only a file download; the installer runs inside the sandbox.
    nvda.download(nvda.VERSION)


def precheck(target: Target) -> None:
    """Check what the daemon would fail on, so the CLI exits with the right code."""
    if target.mode == "shared":
        raise ModeNotBuilt(target.mode)
    if target.mode == "desktop" and target.launch:
        nvda.require_exe(nvda.folder_for(target.nvda_dir))
        nvda.refuse_if_running()
    if target.mode == "sandbox" and target.launch:
        _check_sandbox(target)


def _wait_connected(target: Target, folder: Path) -> dict[str, Any]:
    """For an NVDA that asc launches, wait until it has joined the relay."""
    timeout_s = CONNECT_TIMEOUT_S[target.mode]
    deadline = time.monotonic() + timeout_s
    while True:
        current = status(folder, target.name)
        failed = str(current.get("sandbox") or "")
        if current["connected"]:
            return current
        if failed.startswith("failed") or time.monotonic() > deadline:
            stop(folder, target.name)
            if failed.startswith("failed"):
                raise SandboxFailed("start", failed.removeprefix("failed: "))
            raise NvdaDidNotConnect(timeout_s, log_tail(folder))
        time.sleep(CONNECT_POLL_S)


def start(target: Target, folder: Path) -> dict[str, Any]:
    """Start the daemon and return its status once it is ready."""
    precheck(target)
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
            if target.launch and target.mode in CONNECT_TIMEOUT_S:
                return _wait_connected(target, folder)
            return status(folder, target.name)
        time.sleep(POLL_S)
    raise DaemonStartFailed(None, START_TIMEOUT_S, log_tail(folder))


def stop(folder: Path, target: str) -> None:
    ipc.call(folder, target, ipc.Request("stop", ipc.NoArgs()), STATUS_TIMEOUT_S)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while state.exists(folder) and time.monotonic() < deadline:
        time.sleep(POLL_S)
