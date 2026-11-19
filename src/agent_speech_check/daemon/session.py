"""Starting and stopping the daemon. No printing: `cli.py` shows the results."""

from __future__ import annotations

import errno
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_speech_check.config.targets import Target, check_runnable
from agent_speech_check.daemon import ipc, server, state
from agent_speech_check.daemon.server import LOG_FILE
from agent_speech_check.errors import (
    AlreadyRunning,
    AscError,
    AscStillRunning,
    DaemonNotAnswering,
    DaemonStartFailed,
    DaemonUnstoppable,
    FileUnusable,
    FirewallRuleMissing,
    NotConnected,
    NotRunning,
    NvdaDidNotConnect,
    OwnReaderInInstance,
    PortInUse,
    RemoteNeedsInstance,
    SandboxBusy,
    SharedNeedsConsent,
    SshFailed,
)
from agent_speech_check.nvda import launcher, nvda_config
from agent_speech_check.orca.link import watch_secret
from agent_speech_check.remote import relay
from agent_speech_check.sandbox import firewall, wsb
from agent_speech_check.system import paths, processes, spawn
from agent_speech_check.system.paths import relay_dir
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


def _probe(folder: Path) -> tuple[bool, NotConnected | None]:
    """Whether a daemon runs in the folder, and if it doesn't answer, why.
    A stale state file is removed on the way.

    A daemon that is there but can't be talked to (an older asc, or one that
    doesn't reply) counts as running, so nothing forgets it: `stop` ends it.
    """
    if not state.exists(folder):
        return False, None
    try:
        status(folder)
    except NotRunning:  # a NotConnected too, so it must come first
        return False, None
    except NotConnected as error:
        return True, error
    return True, None


def is_running(folder: Path) -> bool:
    return _probe(folder)[0]


def running_mode(root: Path) -> str | None:
    """The mode whose daemon runs, answering or not. Only one can, but an older
    asc may have left another in its own folder."""
    return next((folder.name for folder in state.folders(root) if is_running(folder)), None)


def _end(folder: Path) -> None:
    """End a daemon that can't be asked to stop. Its NVDA goes with it (a
    kill-on-close job) and so does its sandbox (the guard)."""
    try:
        current = state.read(folder)
    except NotConnected:
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
        err = (folder / ERR_FILE).open("wb")
    except OSError as error:
        raise FileUnusable.from_os(error.filename or folder, error) from None
    with err:
        return spawn.detached(spawn.asc_command("_daemon", target.to_json()), err, folder)


def _check_nvda(target: Target) -> None:
    """The NVDA asc will launch exists, is new enough, and its own config reads
    and takes the mode's settings."""
    folder = launcher.folder_for(target.nvda_dir)
    launcher.require_exe(folder)
    nvda_config.check(target.settings, nvda_config.user_ini(nvda_config.source_config(folder)))


def _check_firewall(target: Target) -> None:
    if not firewall.rule_ok(target.port):
        raise FirewallRuleMissing(target.port, firewall.setup_command())


def _check_sandbox(target: Target) -> None:
    if target.nvda_dir:
        _check_nvda(target)
    wsb.require_available()
    _check_firewall(target)
    if wsb.running():
        raise SandboxBusy
    if target.nvda_dir is None:
        # Only a file download; the installer runs inside the sandbox.
        launcher.latest_installer()


def check_instance(target: Target, command: str) -> None:
    """Refuse a mode this kind of instance doesn't run."""
    named = paths.instance() is not None
    if target.own_reader and named:
        raise OwnReaderInInstance(target.mode)
    if target.named_only and not named:
        raise RemoteNeedsInstance(command)


def precheck(target: Target) -> None:
    """Check what the daemon would fail on, so the CLI exits with the right code."""
    check_runnable(target)
    check_instance(target, "start")
    if target.mode == "shared" and not target.consent:
        raise SharedNeedsConsent
    if target.launches_portable:
        _check_nvda(target)
        launcher.refuse_if_running()
    if target.launches_sandbox:
        _check_sandbox(target)
    # Last: it holds the hotkey for a moment, so only once nothing else refuses.
    if target.takes_panic_key:
        server.check_panic_key(target.panic_key)


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
                raise SshFailed(str(target.ssh), f"no tunnel within {timeout_s:g} s")
            reader = "Orca" if target.orca else "NVDA"
            raise NvdaDidNotConnect(timeout_s, log_tail(folder), reader)
        time.sleep(POLL_S)


def start(target: Target, folder: Path) -> dict[str, Any]:
    """Start the daemon and return its status once it is ready."""
    precheck(target)
    # The daemon loads these too, but an error there reaches the CLI only as an exit code.
    if not target.orca:
        relay.load_or_create(relay_dir(target.mode))
    elif target.mode == "watch":
        watch_secret()
    # Only one daemon runs, whatever its mode, even one that no longer answers.
    for other in state.folders(folder.parent):
        running, silent = _probe(other)
        if silent is not None:
            raise DaemonNotAnswering(silent.message)
        if running:
            raise AlreadyRunning(other.name)
    # A tunnel's port is on the other machine.
    if target.port and not target.tunnel:
        _check_port_free(target)
    _wait_started(_spawn(target, folder), folder)
    if target.launches_reader or target.tunnel:
        return _wait_ready(target, folder)
    return status(folder)


def _wait_started(process: subprocess.Popen[bytes], folder: Path) -> None:
    if not poll_until(
        lambda: process.poll() is not None or state.exists(folder), START_TIMEOUT_S, POLL_S
    ):
        raise DaemonStartFailed(None, START_TIMEOUT_S, log_tail(folder))
    code = process.poll()
    if code is not None:
        raise DaemonStartFailed(code, START_TIMEOUT_S, log_tail(folder))


if sys.platform == "win32":
    _IN_USE = (errno.WSAEADDRINUSE, errno.WSAEACCES)
else:
    _IN_USE = (errno.EADDRINUSE, errno.EACCES)


def _check_port_free(target: Target) -> None:
    """Another instance's relay may hold the port, which the daemon could only
    report as a failed start. Probed on the relay's own address: on Windows a
    socket on 127.0.0.1 doesn't clash with one on the Default Switch."""
    try:
        socket.create_server((server.relay_host(target), target.port)).close()
    except OSError as error:
        # Any other failure, like an address not ours, is the daemon's to report.
        if error.errno not in _IN_USE:
            return
        command = f"asc config set port PORT --mode {target.mode}{paths.instance_flag()}"
        raise PortInUse(target.port, command) from None


def stop(folder: Path, *, timeout_s: float = STOP_TIMEOUT_S) -> None:
    try:
        ipc.call(folder, ipc.Request("stop", ipc.NoArgs()), STATUS_TIMEOUT_S)
    except NotRunning:
        raise
    except NotConnected:
        _end(folder)
        return
    if not poll_until(lambda: not state.exists(folder), timeout_s, POLL_S):
        # It said yes but is still there: end it.
        _end(folder)


def stop_all(
    roots: list[Path], image: Path | None, *, timeout_s: float = STOP_TIMEOUT_S
) -> Iterator[Path]:
    """Stop every daemon under `roots`, for `asc stop` and the installer, yielding
    the folder of each one stopped.

    Tries them all before raising for any it couldn't end. Then waits for the
    other processes running `image`: a stopped daemon still runs for a moment
    after its state file goes, and a sandbox guard outlives its daemon. It never
    ends them, as they may be a person's own asc commands.
    """
    stuck: list[int] = []
    for folder in (folder for root in roots for folder in state.folders(root)):
        try:
            state.read(folder)
        except NotConnected:
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
    if image is not None and not poll_until(
        lambda: not processes.others_running(image), timeout_s, POLL_S
    ):
        raise AscStillRunning(processes.others_running(image))
