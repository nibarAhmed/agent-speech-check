"""Starting and stopping the daemon. No printing: `cli.py` shows the results."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from agent_speech_check.config.targets import Target, check_runnable
from agent_speech_check.daemon import ipc, state
from agent_speech_check.daemon.server import LOG_FILE, START_ERROR_FILE
from agent_speech_check.errors import (
    AlreadyRunning,
    AscError,
    DaemonUnreachable,
    DaemonUnstoppable,
    FileUnusable,
    NotRunning,
    OperationalError,
    SharedNeedsConsent,
    UsageError,
)
from agent_speech_check.nvda import launcher
from agent_speech_check.sandbox import firewall, wsb
from agent_speech_check.system import paths, processes, spawn
from agent_speech_check.system.wait import poll_until

START_TIMEOUT_S = 15.0
# Past the daemon's own worst case: 10 s for NVDA, then its window threads.
STOP_TIMEOUT_S = 30.0
POLL_S = 0.1
ERR_FILE = "daemon.err"
TAIL_LINES = 10
STATUS_TIMEOUT_S = 5.0
# A sandbox boots and makes its portable NVDA first, which takes a while.
CONNECT_TIMEOUT_S = {"watch": 60.0, "desktop": 60.0, "sandbox": 300.0, "remote": 60.0}


def status(folder: Path) -> dict[str, Any]:
    return ipc.call(folder, ipc.Request("status", ipc.NoArgs()), STATUS_TIMEOUT_S)


def call(root: Path, request: ipc.Request, timeout_s: float) -> dict[str, Any]:
    """Send one request to the daemon that runs, wherever its folder is."""
    for folder in state.folders(root):
        try:
            return ipc.call(folder, request, timeout_s)
        except NotRunning:
            # Stale: no daemon got the request, so the next one may.
            continue
    raise NotRunning


def _probe(folder: Path) -> tuple[bool, DaemonUnreachable | None]:
    """Whether a daemon runs in the folder, and if it doesn't answer, why.
    A stale state file is removed on the way.

    A daemon that is there but can't be talked to (an older asc, or one that
    doesn't reply) counts as running, so nothing forgets it: `stop` ends it.
    """
    if not state.exists(folder):
        return False, None
    try:
        status(folder)
    except NotRunning:
        return False, None
    except DaemonUnreachable as error:
        return True, error
    return True, None


def is_running(folder: Path) -> bool:
    return _probe(folder)[0]


def running_mode(root: Path) -> str | None:
    """The mode whose daemon runs, answering or not. Only one can, but an older
    asc may have left another in its own folder."""
    return next((folder.name for folder in state.folders(root) if is_running(folder)), None)


def running_status(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The running daemon's status under `root`, or `(None, mode)` if it doesn't answer.
    Returns `(None, None)` when nothing runs."""
    for folder in state.folders(root):
        try:
            return status(folder), folder.name
        except NotRunning:
            continue
        except DaemonUnreachable:
            return None, folder.name
    return None, None


def _end(folder: Path) -> None:
    """End a daemon that can't be asked to stop. Its NVDA goes with it (a
    kill-on-close job) and so does its sandbox (the guard)."""
    try:
        current = state.read(folder)
    except (NotRunning, DaemonUnreachable):
        # A file this asc can't read names no process to end: only the file goes.
        state.remove(folder)
        return
    try:
        created = processes.created_at(current.pid)
        # A pid is reused only after its process is gone, so a process made after
        # the daemon wrote the file is some other program: leave it alone.
        if created is not None and created <= current.started:
            processes.terminate(current.pid, STOP_TIMEOUT_S)
    except OSError:
        # It may still run, so the file stays: forgetting it would orphan it.
        raise DaemonUnstoppable([current.pid]) from None
    state.remove(folder)


def log_tail(folder: Path) -> str:
    lines: list[str] = []
    for name in (LOG_FILE, ERR_FILE):
        path = folder / name
        if path.exists():
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    return "\n".join(lines)


def _spawn(target: Target, folder: Path) -> subprocess.Popen[bytes]:
    # Shared mode keeps its relay files in watch mode's folder, so its own may not exist yet.
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / START_ERROR_FILE).unlink(missing_ok=True)
        err = (folder / ERR_FILE).open("wb")
    except OSError as error:
        raise FileUnusable.from_os(error.filename or folder, error) from None
    with err:
        return spawn.detached(spawn.asc_command("_daemon", target.to_json()), err, folder)


def _check_firewall(target: Target) -> None:
    if not firewall.rule_ok(target.port):
        raise OperationalError(
            f"no firewall rule lets NVDA in Windows Sandbox reach asc on port {target.port}",
            hint=f"{firewall.setup_command()} (asks for admin rights once)",
        )


def _check_sandbox(target: Target) -> None:
    if target.nvda_dir:
        launcher.check_portable(target.nvda_dir, target.settings)
    wsb.require_available()
    _check_firewall(target)
    if wsb.running():
        raise OperationalError(
            "a Windows Sandbox is already running, and Windows allows only one",
            hint="close it, or asc stop if asc started it",
        )
    if target.nvda_dir is None:
        # Only a file download; the installer runs inside the sandbox.
        launcher.latest_installer()


def check_instance(target: Target, command: str) -> None:
    """Refuse a mode this kind of instance doesn't run."""
    named = paths.instance() is not None
    if target.own_reader and named:
        raise UsageError(
            f"{target.mode} mode uses your own screen reader, "
            "which only the default instance may join",
            hint="use remote mode in a named instance, or leave out --instance",
        )
    if target.named_only and not named:
        raise UsageError(
            "remote mode runs only in a named instance, one per VM",
            hint=(
                f"asc {command} --mode remote --instance VMNAME (asc status --all lists instances)"
            ),
        )


def precheck(target: Target) -> None:
    """Check what can fail cheaply or must happen before spawning the daemon."""
    check_runnable(target)
    check_instance(target, "start")
    if target.mode == "shared" and not target.consent:
        raise SharedNeedsConsent
    if target.launches_sandbox:
        _check_sandbox(target)


def failure(current: dict[str, Any]) -> AscError | None:
    """Why the daemon's sandbox or Orca failed, as it was raised there, or None."""
    raw = current.get("failure")
    return None if raw is None else ipc.error_from(raw, "failure")


def _wait_ready(target: Target, folder: Path) -> dict[str, Any]:
    """Wait until the screen reader asc starts has joined, or the tunnel NVDA joins
    through is up."""
    timeout_s = CONNECT_TIMEOUT_S[target.mode]
    deadline = time.monotonic() + timeout_s
    while True:
        current = status(folder)
        if current["tunnel"]["up"] if target.tunnel else current["connected"]:
            return current
        failed = failure(current)
        if failed or time.monotonic() > deadline:
            stop(folder)
            if failed:
                raise failed
            if target.tunnel:
                raise OperationalError(f"ssh {target.ssh} failed: no tunnel within {timeout_s:g} s")
            tail = log_tail(folder)
            extra = f"; last daemon log lines:\n{tail}" if tail else ""
            name = "Orca" if target.reader == "orca" else "NVDA"
            raise OperationalError(
                f"{name} did not connect within {timeout_s:g} s, so asc stopped{extra}"
            )
        time.sleep(POLL_S)


def start(target: Target, folder: Path) -> dict[str, Any]:
    """Start the daemon and return its status once it is ready."""
    precheck(target)
    # Only one daemon runs, whatever its mode, even one that no longer answers.
    for other in state.folders(folder.parent):
        running, silent = _probe(other)
        if silent is not None:
            raise OperationalError(
                f"asc is running but doesn't answer: {silent.message}",
                hint="asc stop, then asc start",
            )
        if running:
            raise AlreadyRunning(other.name)
    _wait_started(_spawn(target, folder), folder)
    if target.launches_reader or target.tunnel:
        return _wait_ready(target, folder)
    return status(folder)


def _read_start_error(folder: Path) -> AscError | None:
    path = folder / START_ERROR_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ipc.error_from(raw, "daemon startup error")
    except (OSError, ValueError, AscError):
        return None
    finally:
        path.unlink(missing_ok=True)


def _start_failed(exit_code: int | None, folder: Path) -> OperationalError:
    reason = (
        f"not ready after {START_TIMEOUT_S:g} s"
        if exit_code is None
        else f"it exited with code {exit_code}"
    )
    tail = log_tail(folder)
    extra = f"\nlast daemon log lines:\n{tail}" if tail else ""
    return OperationalError(f"the asc daemon did not start: {reason}{extra}")


def _wait_started(process: subprocess.Popen[bytes], folder: Path) -> None:
    if not poll_until(
        lambda: process.poll() is not None or state.exists(folder), START_TIMEOUT_S, POLL_S
    ):
        raise _start_failed(None, folder)
    code = process.poll()
    if code is not None:
        if (error := _read_start_error(folder)) is not None:
            raise error
        raise _start_failed(code, folder)


def stop(folder: Path, *, timeout_s: float = STOP_TIMEOUT_S) -> None:
    try:
        ipc.call(folder, ipc.Request("stop", ipc.NoArgs()), STATUS_TIMEOUT_S)
    except NotRunning:
        raise
    except DaemonUnreachable:
        _end(folder)
        return
    if not poll_until(lambda: not state.exists(folder), timeout_s, POLL_S):
        # It said yes but is still there: end it.
        _end(folder)


def stop_all(
    roots: list[Path],
    images: Sequence[Path] = (),
    *,
    timeout_s: float = STOP_TIMEOUT_S,
) -> Iterator[Path]:
    """Stop every daemon under `roots`, for `asc stop` and uninstall/installers,
    yielding the folder of each one stopped.

    Tries them all before raising for any it couldn't end. Then waits for the
    other processes running `images`: a stopped daemon still runs for a moment
    after its state file goes, and a sandbox guard outlives its daemon. It never
    ends them, as they may be a person's own asc commands.
    """
    stuck: list[int] = []
    for folder in (folder for root in roots for folder in state.folders(root)):
        try:
            state.read(folder)
        except (NotRunning, DaemonUnreachable):
            # Gone already, or a file that names no process to stop.
            state.remove(folder)
            continue
        try:
            stop(folder)
        except NotRunning:
            continue
        except DaemonUnstoppable as error:
            stuck += error.pids
            continue
        yield folder
    if stuck:
        raise DaemonUnstoppable(stuck)
    if images and not poll_until(lambda: not processes.others_running(*images), timeout_s, POLL_S):
        pids = processes.others_running(*images)
        how = "in Task Manager" if sys.platform == "win32" else "with kill"
        raise OperationalError(
            f"asc is still running (pid {', '.join(str(pid) for pid in pids)})",
            hint=f"close it, or end it {how}, then try again",
        )
