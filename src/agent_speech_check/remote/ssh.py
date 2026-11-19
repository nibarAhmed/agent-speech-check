"""ssh as remote mode runs it: the tunnel NVDA connects back through, and what Orca's
helper shares. ssh's stderr stays in memory: it is shown in an error, never logged."""

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

from agent_speech_check.errors import (
    AscError,
    PortInUse,
    SetupNeeded,
    SshFailed,
    SshMissing,
    SshSetupNeeded,
)
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
# What `-v` adds without a `debug` prefix, which never says why ssh ended.
_CHATTER = ("OpenSSH", "Authenticated to", "Transferred:", "Bytes per second:")
# Only `-v` says when the other machine listens: this debug line.
_FORWARDED = "remote forward success for: listen"
_FORWARD_FAILED = "remote port forwarding failed"
RETRY_FIRST_S = 1.0
RETRY_MAX_S = 30.0


def command(destination: str, *args: str) -> list[str]:
    return ["ssh", *args, *OPTIONS, "--", destination]


def tunnel_command(destination: str, port: int, local_port: int) -> list[str]:
    forward = f"127.0.0.1:{port}:127.0.0.1:{local_port}"
    return command(destination, "-v", "-N", "-o", "ExitOnForwardFailure=yes", "-R", forward)


def failed(destination: str, lines: Sequence[str], code: int) -> AscError:
    """Why ssh exited with `code`, from its last lines of stderr."""
    said = [line for line in lines if not line.startswith(_CHATTER)]
    detail = said[-1] if said else f"exit code {code}"
    if any(sign in line for line in lines for sign in _SETUP):
        return SshSetupNeeded(destination, detail)
    return SshFailed(destination, detail)


async def stderr_lines(stderr: asyncio.StreamReader) -> AsyncIterator[str]:
    """ssh's stderr, line by line, each cut to 500 characters."""
    while True:
        try:
            raw = await stderr.readline()
        except ValueError:
            continue  # an overlong line is dropped
        if not raw:
            return
        yield raw.decode(errors="replace").rstrip()[:500]


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
            came_up, error = await self._once()
            ever_up = ever_up or came_up
            self.up = False
            self.last_error = error.message
            if not ever_up or isinstance(error, SetupNeeded):
                self.failure = error
                log.warning("ssh tunnel failed: %s", type(error).__name__)
                return
            delay = RETRY_FIRST_S if came_up else min(delay * 2, RETRY_MAX_S)
            log.info("ssh tunnel lost (%s), again in %g s", type(error).__name__, delay)
            await asyncio.sleep(delay)

    async def _once(self) -> tuple[bool, AscError]:
        """One ssh, start to exit: whether it came up, and why it ended."""
        try:
            process = await asyncio.create_subprocess_exec(
                *tunnel_command(self.destination, self.port, self.local_port),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **_dies_with_asc(),
            )
        except OSError as error:
            return False, SshMissing(error.strerror or str(error))
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
            return came_up, self.error(await process.wait(), lines)
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

    def error(self, code: int, lines: Sequence[str]) -> AscError:
        if code == FAILED and any(_FORWARD_FAILED in line for line in lines):
            fix = f"asc config set port PORT --mode remote{paths.instance_flag()}"
            return PortInUse(self.port, fix)
        return failed(self.destination, lines, code)
