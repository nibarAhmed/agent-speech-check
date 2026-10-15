"""Starting and stopping a target's daemon. No printing: `cli.py` shows the results."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from agent_speech_check import (
    daemon,
    firewall,
    ipc,
    nvda,
    nvda_config,
    sandbox,
    spawn,
    state,
    winapi,
)
from agent_speech_check.daemon import LOG_FILE
from agent_speech_check.errors import (
    AlreadyRunning,
    AscError,
    DaemonNotAnswering,
    DaemonStartFailed,
    DaemonUnstoppable,
    FirewallRuleMissing,
    NotConnected,
    NotRunning,
    NvdaDidNotConnect,
    SandboxBusy,
    SharedNeedsConsent,
)
from agent_speech_check.targets import Target
from agent_speech_check.wait import poll_until

START_TIMEOUT_S = 15.0
# Past the daemon's own worst case: 10 s for NVDA, then its window threads.
STOP_TIMEOUT_S = 30.0
POLL_S = 0.1
ERR_FILE = "daemon.err"
TAIL_LINES = 10
STATUS_TIMEOUT_S = 5.0
# A sandbox boots and makes its portable NVDA first, which takes a while.
CONNECT_TIMEOUT_S = {"desktop": 60.0, "sandbox": 300.0}
CONNECT_POLL_S = 1.0


def status(folder: Path, target: str) -> dict[str, Any]:
    return ipc.call(folder, target, ipc.Request("status", ipc.NoArgs()), STATUS_TIMEOUT_S)


def _probe(folder: Path, target: str) -> tuple[bool, NotConnected | None]:
    """Whether a daemon runs for the target, and if it doesn't answer, why.
    A stale state file is removed on the way.

    A daemon that is there but can't be talked to (an older asc, or one that
    doesn't reply) counts as running, so nothing forgets it: `stop` ends it.
    """
    if not state.exists(folder):
        return False, None
    try:
        status(folder, target)
    except NotRunning:  # a NotConnected too, so it must come first
        return False, None
    except NotConnected as error:
        return True, error
    return True, None


def is_running(folder: Path, target: str) -> bool:
    return _probe(folder, target)[0]


def running_targets(root: Path) -> list[str]:
    """Every target whose daemon runs, answering or not, by name."""
    return sorted(
        f.parent.name for f in root.glob(f"*/{state.FILE}") if is_running(f.parent, f.parent.name)
    )


def _end(folder: Path, target: str) -> None:
    """End a daemon that can't be asked to stop. Its NVDA goes with it (a
    kill-on-close job) and so does its sandbox (the guard)."""
    try:
        current = state.read(folder, target)
    except NotConnected:
        # A file this asc can't read names no process to end: only the file goes.
        state.remove(folder)
        return
    try:
        created = winapi.created_at(current.pid)
        # A pid is reused only after its process is gone, so a process made after
        # the daemon wrote the file is some other program: leave it alone.
        if created is not None and created <= current.started:
            winapi.terminate(current.pid, STOP_TIMEOUT_S)
    except OSError:
        # It may still run, so the file stays: forgetting it would orphan it.
        raise DaemonUnstoppable(target, current.pid) from None
    state.remove(folder)


def log_tail(folder: Path) -> str:
    lines: list[str] = []
    for name in (LOG_FILE, ERR_FILE):
        path = folder / name
        if path.exists():
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    return "\n".join(lines)


def _spawn(target: Target, folder: Path) -> subprocess.Popen[bytes]:
    with (folder / ERR_FILE).open("wb") as err:
        return spawn.detached(spawn.asc_command("_daemon", target.to_json()), err, folder)


def _check_nvda(target: Target) -> None:
    """The NVDA asc will launch exists, is new enough, and its own config reads
    and takes the target's settings."""
    folder = nvda.folder_for(target.nvda_dir)
    nvda.require_exe(folder)
    nvda_config.check(target.settings, nvda_config.user_ini(nvda_config.source_config(folder)))


def _check_sandbox(target: Target) -> None:
    if target.nvda_dir:
        _check_nvda(target)
    sandbox.require_available()
    if not firewall.rule_ok(target.port, firewall.daemon_program()):
        raise FirewallRuleMissing
    if sandbox.running():
        raise SandboxBusy
    if target.nvda_dir is None:
        # Only a file download; the installer runs inside the sandbox.
        nvda.download(nvda.VERSION)


def precheck(target: Target) -> None:
    """Check what the daemon would fail on, so the CLI exits with the right code."""
    if target.mode == "shared":
        if not target.consent:
            raise SharedNeedsConsent
        daemon.check_panic_key()
    if target.launches_portable:
        _check_nvda(target)
        nvda.refuse_if_running()
    if target.launches_sandbox:
        _check_sandbox(target)


def sandbox_failure(current: dict[str, Any]) -> AscError | None:
    """Why the daemon's sandbox failed, as it was raised there, or None."""
    raw = current.get("sandbox_failure")
    return None if raw is None else ipc.error_from(raw, "sandbox_failure")


def _wait_connected(target: Target, folder: Path) -> dict[str, Any]:
    """For an NVDA that asc launches, wait until it has joined the relay."""
    timeout_s = CONNECT_TIMEOUT_S[target.mode]
    deadline = time.monotonic() + timeout_s
    while True:
        current = status(folder, target.name)
        if current["connected"]:
            return current
        failure = sandbox_failure(current)
        if failure or time.monotonic() > deadline:
            stop(folder, target.name)
            if failure:
                raise failure
            raise NvdaDidNotConnect(timeout_s, log_tail(folder))
        time.sleep(CONNECT_POLL_S)


def start(target: Target, folder: Path) -> dict[str, Any]:
    """Start the daemon and return its status once it is ready."""
    precheck(target)
    folder.mkdir(parents=True, exist_ok=True)
    running, silent = _probe(folder, target.name)
    if silent is not None:
        raise DaemonNotAnswering(target.name, silent.message)
    if running:
        raise AlreadyRunning(target.name)
    process = _spawn(target, folder)
    if not poll_until(
        lambda: process.poll() is not None or state.exists(folder), START_TIMEOUT_S, POLL_S
    ):
        raise DaemonStartFailed(None, START_TIMEOUT_S, log_tail(folder))
    code = process.poll()
    if code is not None:
        raise DaemonStartFailed(code, START_TIMEOUT_S, log_tail(folder))
    if target.launches_nvda:
        return _wait_connected(target, folder)
    return status(folder, target.name)


def stop(folder: Path, target: str, *, timeout_s: float = STOP_TIMEOUT_S) -> None:
    try:
        ipc.call(folder, target, ipc.Request("stop", ipc.NoArgs()), STATUS_TIMEOUT_S)
    except NotRunning:
        raise
    except NotConnected:
        _end(folder, target)
        return
    if not poll_until(lambda: not state.exists(folder), timeout_s, POLL_S):
        # It said yes but is still there: end it.
        _end(folder, target)
