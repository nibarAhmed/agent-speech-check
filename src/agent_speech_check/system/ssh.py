"""ssh as remote mode runs it: the tunnel NVDA connects back through, what Orca's
helper shares, and one-off commands like `asc keys`. ssh's stderr stays in memory:
it is shown in an error, never logged."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import signal
import subprocess
import sys
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

from agent_speech_check.errors import AscError, OperationalError, PortInUse, SshMissing
from agent_speech_check.system import paths, winapi

log = logging.getLogger(__name__)

# Keepalives, so a connection that drops silently ends ssh (code 255) in about 15 s.
OPTIONS = ("-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3")
FAILED = 255
STDERR_LINES = 20
# What ssh prints when only the user can fix it, by answering ssh once or fixing the name.
_SETUP = (
    "Permission denied",
    "Host key verification failed",
    "REMOTE HOST IDENTIFICATION HAS CHANGED",
    "Could not resolve hostname",
)
# What ssh says without a `debug` prefix that never says why a command ended.
_CHATTER = (
    "OpenSSH",
    "Authenticated to",
    "Transferred:",
    "Bytes per second:",
    "Warning: Permanently added",
)
# Only `-v` says when the other machine listens: this debug line.
_FORWARDED = "remote forward success for: listen"
_FORWARD_FAILED = "remote port forwarding failed"
RUN_TIMEOUT_S = 60.0
# Its own connection, and the fingerprint asc knows the machine by, whatever ssh_config says.
_FRESH = ("-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "FingerprintHash=sha256")
RETRY_FIRST_S = 1.0
RETRY_MAX_S = 30.0


def command(destination: str, *args: str) -> list[str]:
    return ["ssh", *args, *OPTIONS, "--", destination]


def tunnel_command(destination: str, port: int, local_port: int) -> list[str]:
    forward = f"127.0.0.1:{port}:127.0.0.1:{local_port}"
    return command(destination, "-v", "-N", "-o", "ExitOnForwardFailure=yes", "-R", forward)


def is_setup_error(lines: Sequence[str]) -> bool:
    """True when ssh's stderr names an error only the user can fix."""
    return any(sign in line for line in lines for sign in _SETUP)


def failed(destination: str, lines: Sequence[str], code: int) -> OperationalError:
    """Why ssh exited with `code`, from its last lines of stderr."""
    detail = last_said(lines, code)
    if is_setup_error(lines):
        return OperationalError(
            f"ssh {destination} failed: {detail}",
            hint=f"run 'ssh {destination}' manually once: asc needs key auth, not a password",
        )
    return OperationalError(f"ssh {destination} failed: {detail}")


def last_said(lines: Sequence[str], code: int) -> str:
    said = [line for line in lines if not line.startswith(_CHATTER)]
    return said[-1] if said else f"exit code {code}"


def _clip(line: str) -> str:
    return line.rstrip()[:500]


def run(destination: str, remote: str, data: bytes = b"") -> tuple[int, bytes, str]:
    """Run the command line `remote` there, with `data` as its stdin: its exit code, its
    stdout, and the last line of its stderr. ssh's own failure raises."""
    try:
        done = subprocess.run(  # noqa: S603 - ssh, to the user's own destination
            [*command(destination, "-T"), remote],
            input=data,
            capture_output=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
    except OSError as error:
        raise SshMissing(error.strerror or str(error)) from None
    except subprocess.TimeoutExpired:
        raise OperationalError(
            f"ssh {destination} failed: no answer in {RUN_TIMEOUT_S:.0f} s"
        ) from None
    stderr = done.stderr.decode(errors="replace").splitlines()[-STDERR_LINES:]
    lines = [_clip(line) for line in stderr]
    if done.returncode == FAILED:
        raise failed(destination, lines, FAILED)
    return done.returncode, done.stdout, last_said(lines, done.returncode)


async def run_fresh(destination: str, remote: str) -> tuple[int, bytes, list[str]]:
    """Like `run`, but on a connection of its own, which shows the other machine's host key
    even when ssh shares connections, with every line of stderr (`-v` debug lines too), and
    ssh killed if this is cancelled."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command(destination, "-T", "-v", *_FRESH),
            remote,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_dies_with_asc(),
        )
    except OSError as error:
        raise SshMissing(error.strerror or str(error)) from None
    try:
        async with asyncio.timeout(RUN_TIMEOUT_S):
            stdout, stderr = await process.communicate()
    except TimeoutError:
        raise OperationalError(
            f"ssh {destination} failed: no answer in {RUN_TIMEOUT_S:.0f} s"
        ) from None
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
    code = await process.wait()
    lines = [_clip(line) for line in stderr.decode(errors="replace").splitlines()]
    if code == FAILED:
        raise failed(destination, said(lines), FAILED)
    return code, stdout, lines


def said(lines: Sequence[str]) -> list[str]:
    """What ssh said, without `-v`'s debug lines."""
    return [line for line in lines if not line.startswith("debug")][-STDERR_LINES:]


async def stderr_lines(stderr: asyncio.StreamReader) -> AsyncIterator[str]:
    """ssh's stderr, line by line, each cut to 500 characters."""
    while True:
        try:
            raw = await stderr.readline()
        except ValueError:
            continue  # an overlong line is dropped
        if not raw:
            return
        yield _clip(raw.decode(errors="replace"))


def _dies_with_asc() -> dict[str, Any]:
    """With `-N` ssh never reads stdin, so only this ends it when asc crashes. On Linux the
    loop thread, which lives as long as asc, forks it."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    prctl = ctypes.CDLL(None).prctl  # looked up before the fork: the child may only call it
    pr_set_pdeathsig = 1
    return {"preexec_fn": lambda: prctl(pr_set_pdeathsig, signal.SIGKILL)}


class Tunnel:
    """`ssh -R`: `port` on the other machine, where NVDA connects, reaches the relay here."""

    def __init__(self, destination: str, port: int, local_port: int) -> None:
        self.destination = destination
        self.port = port
        self.local_port = local_port
        self.up = False
        self.last_error: str | None = None
        # Why it never came up, for `asc start` to raise.
        self.failure: AscError | None = None

    def state(self) -> dict[str, Any]:
        return {"up": self.up, "error": self.last_error}

    async def run(self) -> None:
        """Keep the tunnel up until cancelled. Never raises: one that never came up, or
        that only the user can fix, goes to `failure`; a lost one is made again."""
        delay = RETRY_FIRST_S
        ever_up = False
        while True:
            came_up, retryable, error = await self._once()
            ever_up = ever_up or came_up
            self.up = False
            self.last_error = error.message
            if not ever_up or not retryable:
                self.failure = error
                reason = (
                    type(error).__name__
                    if type(error) is not OperationalError
                    else ("setup" if not retryable else "ssh")
                )
                log.warning("ssh tunnel failed: %s", reason)
                return
            delay = RETRY_FIRST_S if came_up else min(delay * 2, RETRY_MAX_S)
            log.info("ssh tunnel lost, again in %g s", delay)
            await asyncio.sleep(delay)

    async def _once(self) -> tuple[bool, bool, AscError]:
        """One ssh, start to exit: whether it came up, whether to retry, and why it ended."""
        try:
            process = await asyncio.create_subprocess_exec(
                *tunnel_command(self.destination, self.port, self.local_port),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **_dies_with_asc(),
            )
        except OSError as error:
            return False, False, SshMissing(error.strerror or str(error))
        job = None
        try:
            if sys.platform == "win32":
                try:
                    job = winapi.kill_on_close_job(process.pid)
                except OSError:
                    # Windows refuses a process that has already exited; stderr still says why.
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
            came_up, lines = await self._follow(process)
            code = await process.wait()
            return came_up, not is_setup_error(lines), self.error(code, lines)
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            if job is not None:
                winapi.close_handle(job)

    async def _follow(self, process: asyncio.subprocess.Process) -> tuple[bool, deque[str]]:
        stderr = process.stderr
        if stderr is None:
            raise RuntimeError
        came_up = False
        lines: deque[str] = deque(maxlen=STDERR_LINES)
        async for line in stderr_lines(stderr):
            if not line.startswith("debug"):
                lines.append(line)
            elif _FORWARDED in line and not came_up:
                came_up = self.up = True
                self.last_error = None
                log.info("ssh tunnel up")
        return came_up, lines

    def error(self, code: int, lines: Sequence[str]) -> OperationalError:
        if code == FAILED and any(_FORWARD_FAILED in line for line in lines):
            fix = f"asc config set port PORT --mode remote{paths.instance_flag()}"
            return PortInUse(self.port, fix)
        return failed(self.destination, lines, code)
