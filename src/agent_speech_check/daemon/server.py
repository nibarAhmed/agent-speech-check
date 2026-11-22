"""The background process for one mode: relay, speech log, keys and waits.

`asc start` runs it as `asc _daemon <settings json>`. It writes the state file
once it listens, runs until `asc stop` (or Ctrl+Break), then releases any held
keys and removes the state file. If a long-lived task crashes, it logs the
error and exits rather than keep running half dead.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import functools
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

from agent_speech_check.config.targets import Target
from agent_speech_check.daemon import ipc, state
from agent_speech_check.daemon.speech_log import Entry, Since, SpeechLog
from agent_speech_check.errors import (
    AlreadyRunning,
    AscError,
    LaunchFailed,
    NoMatch,
    OperationalError,
    PortInUse,
    SharedNeedsConsent,
    UsageError,
    numbered_lines,
)
from agent_speech_check.inputs import mouse
from agent_speech_check.inputs.keyboard import Keyboard, Link
from agent_speech_check.inputs.keys import Hotkey
from agent_speech_check.nvda import launcher, nvda_config, relay
from agent_speech_check.nvda.relay import Relay
from agent_speech_check.orca.link import OrcaLink, watch_secret
from agent_speech_check.system import paths, winapi
from agent_speech_check.system.paths import mode_dir, relay_dir, state_root, write_atomic
from agent_speech_check.system.ssh import Tunnel

if sys.platform != "win32":
    import fcntl

log = logging.getLogger(__name__)

LOG_FILE = "daemon.log"
START_ERROR_FILE = "start_error.json"
STOP_DELAY_S = 0.05

# Windows says WSAEACCES for a port another socket holds exclusively; Linux's EACCES
# is a privileged port instead.
if sys.platform == "win32":
    _IN_USE = (errno.WSAEADDRINUSE, errno.WSAEACCES)
else:
    _IN_USE = (errno.EADDRINUSE,)


class LogFormatter(logging.Formatter):
    """Writes a traceback's frames and exception types, never their messages: a
    message can hold speech or a request's text. Every traceback in the log
    comes through here, asyncio's own included."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        # Never the traceback text another handler cached on the record, messages and all.
        cached, record.exc_text = record.exc_text, None
        try:
            return super().format(record)
        finally:
            record.exc_text = cached

    def formatException(self, ei: Any) -> str:
        error = ei[1]
        if not isinstance(error, BaseException):
            return super().formatException(ei)
        return "".join(_exception_lines(error, set())).rstrip()


def _exception_lines(error: BaseException, seen: set[int]) -> list[str]:
    if id(error) in seen:
        return []
    seen.add(id(error))
    kind = type(error)
    lines = [
        "Traceback (most recent call last):\n",
        *traceback.format_tb(error.__traceback__),
        f"{kind.__module__}.{kind.__qualname__} (message not logged)\n",
    ]
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            lines += _exception_lines(inner, seen)
    cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    if cause is not None:
        lines += ["caused by:\n", *_exception_lines(cause, seen)]
    return lines


def _setup_logging(folder: Path) -> None:
    handler = RotatingFileHandler(
        folder / LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(LogFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def take_panic_key(key: Hotkey, on_press: Callable[[], None]) -> winapi.GlobalHotkey:
    """asc never sends keys into this Windows session without a way for the user to
    stop it at once: the panic hotkey releases held keys and stops asc."""
    hotkey = winapi.GlobalHotkey(key.modifiers, key.vk, on_press)
    if not hotkey.start():
        raise OperationalError(
            f"cannot register the panic hotkey {key.text}: another program owns it",
            hint="asc never sends keys to this desktop without one; close that program, "
            "or pick another key with asc config set panic_key",
        )
    return hotkey


def start_app(command: str) -> None:
    """Start the app under test, detached. Popen returns at once."""
    try:
        subprocess.Popen(  # noqa: S603 - the user's own app, run as asked
            command,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise LaunchFailed(command, error.strerror or str(error)) from None


def new_windows(before: set[int]) -> list[str]:
    """Titles of the titled windows that weren't open in `before`. A window
    Windows kept behind (its focus rules) is listed, though NVDA never hears it."""
    titles = (winapi.window_title(h) for h in winapi.windows_titled("") if h not in before)
    return [title for title in titles if title]


# Half a reply for speech lines leaves the rest for the envelope around them.
SPEECH_BUDGET = ipc.MAX_RESPONSE // 2


def _fit(since: Since) -> tuple[list[list[Any]], int]:
    """The newest lines that fit in one reply as `[n, line]` pairs, and how many
    lines count as dropped: those the log lost, plus the older ones left out."""
    lines: list[list[Any]] = []
    size = 0
    for entry in reversed(since.entries):
        pair = [entry.n, entry.line()]
        # As the reply's JSON escapes it, with its separator. An error message
        # holds the same text with less around it, so this covers both.
        size += len(json.dumps(pair)) + 2
        if size > SPEECH_BUDGET:
            break
        lines.append(pair)
    lines.reverse()
    return lines, since.dropped + len(since.entries) - len(lines)


def _new_speech(since: Since, after: int) -> dict[str, Any]:
    """A command's own speech, and the line it came after for when there is none."""
    lines, dropped = _fit(since)
    return {"lines": lines, "after": after, "dropped": dropped}


class Daemon:
    def __init__(self, target: Target, folder: Path) -> None:
        self.target = target
        self.folder = folder
        self.speech = SpeechLog()
        # The relay NVDA joins, which `run` listens on. Nothing listens for Orca.
        self.relay: Relay | None = None
        self.tunnel: Tunnel | None = None
        self.link: Link
        if target.reader == "orca":
            watch = watch_secret() if target.mode == "watch" else None
            self.link = OrcaLink(target.ssh, self.speech, watch)
        else:
            credentials = relay.load_or_create(relay_dir(target.mode))
            self.link = self.relay = Relay(credentials, self.speech)
        self.keyboard = Keyboard(target, self.link, self.speech)
        self.stopping = asyncio.Event()
        self.nvda: launcher.Running | None = None
        self.panic: winapi.GlobalHotkey | None = None
        self.started = False
        # Commands in progress, in no set order. Status shows them, so a client can tell a
        # command has begun: each one takes its starting point before its first await.
        self.running: list[str] = []
        self.handlers: dict[str, Callable[[Any], Awaitable[dict[str, Any]]]] = {
            "status": self._status,
            "stop": self._stop,
            "mark": self._mark,
            "speech": self._speech,
            "wait": self._wait,
            "press": self._press,
            "type": self._type,
            "launch": self._launch,
        }

    async def handle(self, request: ipc.Request) -> dict[str, Any]:
        handler = self.handlers[request.cmd]
        if request.cmd == "status":
            return await handler(request.args)
        self.running.append(request.cmd)
        try:
            return await handler(request.args)
        finally:
            self.running.remove(request.cmd)

    async def _status(self, _args: ipc.NoArgs) -> dict[str, Any]:
        return self.status()

    async def _stop(self, _args: ipc.NoArgs) -> dict[str, Any]:
        asyncio.get_running_loop().call_later(STOP_DELAY_S, self.stopping.set)
        return {}

    async def _mark(self, _args: ipc.NoArgs) -> dict[str, Any]:
        return {"mark": self.speech.latest}

    def _check_since(self, since: int) -> None:
        """A line not said yet can only be a number from an earlier session."""
        if since > self.speech.latest:
            raise UsageError(
                f"--since {since} is after the latest line, {self.speech.latest}",
                hint="asc mark (line numbers restart with each asc start)",
            )

    async def _speech(self, args: ipc.SpeechArgs) -> dict[str, Any]:
        self._check_since(args.since)
        lines, dropped = _fit(self.speech.since(args.since))
        return {"lines": lines, "dropped": dropped}

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.target.mode,
            "keys": self.target.keys,
            "reader": self.target.reader,
            "ssh": self.target.ssh,
            "tunnel": None if self.tunnel is None else self.tunnel.state(),
            "connected": self.link.connected,
            **self._address(),
            "browse_focus": self.speech.mode,
            "lines": len(self.speech),
            "latest": self.speech.latest,
            "last_error": self.link.last_error,
            "failure": self._failure(),
            "panic_key": None if self.panic is None else self.target.panic_key.text,
            "running": list(self.running),
        }

    def _address(self) -> dict[str, Any]:
        """Where NVDA connects: on the other machine, for a tunnel. None for Orca."""
        address: dict[str, Any] = dict.fromkeys(("host", "port", "channel", "fingerprint"))
        address.update(self.link.status_fields())
        if self.tunnel is not None:
            address["port"] = self.target.port
        return address

    def _failure(self) -> dict[str, Any] | None:
        """Why Orca or the tunnel never got going, for `asc start` to raise."""
        owners = (self.link, self.tunnel)
        failures = (owner.failure for owner in owners if owner is not None)
        failure = next((f for f in failures if f is not None), None)
        return None if failure is None else ipc.error_detail(failure)

    async def _wait(self, args: ipc.WaitArgs) -> dict[str, Any]:
        self._check_since(args.since)
        if not self.link.connected:
            raise self.link.not_connected
        losses_before = len(self.link.losses)
        entry = await self._wait_match(args.regex, args.since, args.timeout_ms, losses_before)
        if entry is None:
            raise self._no_match(args.pattern, args.timeout_ms, args.since)
        return {"line": [entry.n, entry.line()]}

    async def _wait_match(
        self, pattern: re.Pattern[str], after: int, timeout_ms: int, losses_before: int
    ) -> Entry | None:
        """Like `SpeechLog.wait_match`, but fails on a protocol error from NVDA
        after `losses_before` (`len(relay.losses)` when the command began).

        A plain drop doesn't fail it: NVDA reconnects by itself about 5 s later.
        Orca never comes back, so with Orca any loss fails it.
        """
        check = functools.partial(self.link.check, losses_before, drop_fails=False)
        return await self.speech.wait_match(pattern, after, timeout_ms / 1000, check)

    def _no_match(
        self, pattern: str, timeout_ms: int, after: int, windows: list[str] | None = None
    ) -> AscError:
        """Nothing matched in time: say what the screen reader said, or that it went away."""
        if not self.link.connected:
            return self.link.not_connected(timeout_ms, self.link.last_error)
        lines, dropped = _fit(self.speech.since(after))
        said = numbered_lines(lines, after, dropped)
        return NoMatch(pattern, timeout_ms, said, windows, self.target.reader)

    async def _press(self, args: ipc.PressArgs) -> dict[str, Any]:
        pressed = await self.keyboard.press(
            args.parsed, args.settle_ms / 1000, args.timeout_ms / 1000, args.regex
        )
        if not pressed.matched:
            # A drop fails a press before this, so this lists speech, not a lost NVDA.
            raise self._no_match(args.expect or "", args.timeout_ms, pressed.after)
        return _new_speech(pressed.since, pressed.after)

    async def _type(self, args: ipc.TypeArgs) -> dict[str, Any]:
        typed = await self.keyboard.press(
            self.link.gestures(args.text),
            args.settle_ms / 1000,
            args.timeout_ms / 1000,
            settle_each=False,
        )
        return _new_speech(typed.since, typed.after)

    async def _launch(self, args: ipc.LaunchArgs) -> dict[str, Any]:
        if self.target.tunnel:
            raise OperationalError(
                "remote mode can't start apps on Windows: NVDA runs on another machine",
                hint="start the app there, then asc wait for its speech",
            )
        if not self.link.connected:
            raise self.link.not_connected
        start = self.speech.latest
        losses_before = len(self.link.losses)
        # Orca's windows can't be seen from here.
        before = set(winapi.windows_titled("")) if self.relay is not None else None
        if isinstance(self.link, OrcaLink):
            await self.link.launch(args.command)
        else:
            # Input only where the agent may send it: in watch mode the user drives.
            if self.target.keys and not mouse.nudge():
                log.info("launch: Windows may keep the app behind")
            start_app(args.command)
        # One deadline for the title and the quiet after it, so the CLI's
        # timeout (--timeout plus ipc.LAUNCH_START_MS) always covers both.
        deadline = time.monotonic() + args.timeout_ms / 1000
        title = args.regex
        if title is None:
            # No title given: wait for the first speech, then for quiet.
            title = re.compile(".")
        found = await self._wait_match(title, start, args.timeout_ms, losses_before)
        if found is None:
            windows = None if before is None else new_windows(before)
            raise self._no_match(args.title or ".", args.timeout_ms, start, windows)
        remaining = max(0.0, deadline - time.monotonic())
        await self.speech.wait_quiet(ipc.DEFAULT_SETTLE_MS / 1000, remaining)
        return _new_speech(self.speech.since(start), start)

    def _relay_info(self) -> nvda_config.RelayAddress:
        if self.relay is None:  # only an NVDA that asc launches asks, never Orca
            raise RuntimeError
        credentials = self.relay.credentials
        return nvda_config.RelayAddress(
            self.relay.host, self.relay.port, credentials.channel, credentials.fingerprint
        )

    def _check_nvda(self) -> None:
        launcher.check_portable(self.target.nvda_dir, self.target.settings)
        launcher.refuse_if_running()

    def _start_nvda(self) -> None:
        if self.target.launches_nvda:
            self.nvda = launcher.launch(
                launcher.folder_for(self.target.nvda_dir),
                self.folder / "nvda_config",
                self._relay_info(),
                self.target.settings,
            )

    def _start_panic_key(self) -> None:
        if self.target.mode == "shared" and not self.target.consent:
            raise SharedNeedsConsent
        if not self.target.takes_panic_key:
            return
        loop = asyncio.get_running_loop()

        def pressed() -> None:
            loop.call_soon_threadsafe(self._on_panic)

        self.panic = take_panic_key(self.target.panic_key, pressed)

    def _on_panic(self) -> None:
        log.warning("panic hotkey pressed: stopping")
        self.stopping.set()

    async def _start_relay(self, relay: Relay) -> asyncio.Server:
        # A tunnel's port is on the other machine; here any free one does.
        try:
            server = await relay.start(0 if self.target.tunnel else self.target.port)
        except OSError as error:
            if self.target.port and not self.target.tunnel and error.errno in _IN_USE:
                command = (
                    f"asc config set port PORT --mode {self.target.mode}{paths.instance_flag()}"
                )
                raise PortInUse(self.target.port, command) from None
            raise
        log.info("relay on %s", relay.port)
        if self.target.tunnel and self.target.ssh is not None:
            self.tunnel = Tunnel(self.target.ssh, self.target.port, relay.port)
        return server

    async def run(self) -> None:
        relay_server: asyncio.Server | None = None
        ipc_server: asyncio.Server | None = None
        tunnel: asyncio.Task[None] | None = None
        try:
            if self.relay is not None:
                relay_server = await self._start_relay(self.relay)
            if self.target.launches_nvda:
                await asyncio.to_thread(self._check_nvda)
            self._start_panic_key()
            token = secrets.token_urlsafe(32)
            ipc_server = await asyncio.start_server(
                functools.partial(ipc.serve_one, token, self.handle),
                "127.0.0.1",
                0,
                limit=ipc.MAX_REQUEST,
            )
            port = ipc_server.sockets[0].getsockname()[1]
            log.info("IPC on %d", port)
            # Outside the group: it ends after the relay, so key releases still reach NVDA.
            if self.tunnel is not None:
                tunnel = asyncio.create_task(self.tunnel.run())
                tunnel.add_done_callback(self._stop_if_crashed)
            # Off the loop: copying the user's config (add-ons too) can take a while.
            await asyncio.to_thread(self._start_nvda)
            state.State(os.getpid(), port, token, time.time()).write(self.folder)
            self.started = True
            async with asyncio.TaskGroup() as group:
                follow = group.create_task(self.link.run())
                await self.stopping.wait()
                follow.cancel()
        finally:
            await self._cleanup(relay_server, ipc_server, tunnel)

    async def _cleanup(
        self,
        relay_server: asyncio.Server | None,
        ipc_server: asyncio.Server | None,
        tunnel: asyncio.Task[None] | None,
    ) -> None:
        self.keyboard.release_all()
        # Before NVDA goes, so it can still answer the close.
        await self.link.stop()
        if self.nvda is not None:
            self.nvda.stop()
        if relay_server is not None:
            relay_server.close()
        if ipc_server is not None:
            ipc_server.close()
        if self.started:
            state.remove(self.folder)
        if self.panic is not None:
            self.panic.stop()
        if tunnel is not None:
            tunnel.cancel()
            # Last, so a crash it raises skips no cleanup. wait() lets our own cancel through.
            await asyncio.wait([tunnel])
            if not tunnel.cancelled():
                tunnel.result()
        log.info("stopped")

    def _stop_if_crashed(self, task: asyncio.Task[None]) -> None:
        """A crash ends the daemon, as one in the task group does."""
        if not task.cancelled() and task.exception() is not None:
            self.stopping.set()


def _on_break(loop: asyncio.AbstractEventLoop, daemon: Daemon) -> None:
    loop.call_soon_threadsafe(daemon.stopping.set)


async def _main(daemon: Daemon) -> None:
    loop = asyncio.get_running_loop()
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, lambda *_: _on_break(loop, daemon))
    else:
        loop.add_signal_handler(signal.SIGTERM, daemon.stopping.set)
    await daemon.run()


def _only_daemon(root: Path) -> int | IO[bytes] | None:
    """Take the lock that lets one daemon run per state folder, held until this
    process exits. None if another daemon holds it."""
    if sys.platform == "win32":
        return winapi.take_mutex(winapi.mutex_name(root))
    else:  # not after the return: mypy on Windows would check fcntl
        lock = (root / "daemon.lock").open("ab")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        return lock


def run(target_json: str) -> int:
    """Entry point for `asc _daemon`. Returns the process exit code."""
    target = Target.from_json(target_json)
    folder = mode_dir(target.mode)
    folder.mkdir(parents=True, exist_ok=True)
    _setup_logging(folder)
    daemon: Daemon | None = None
    try:
        lock = _only_daemon(state_root())
        if lock is None:
            raise AlreadyRunning
        log.info("starting %s mode", target.mode)
        daemon = Daemon(target, folder)
        asyncio.run(_main(daemon))
    except AscError as error:
        if daemon is None or not daemon.started:
            with contextlib.suppress(AscError):
                write_atomic(folder / START_ERROR_FILE, json.dumps(ipc.error_detail(error)))
        raise
    except OSError as error:
        log.error("could not start: %s", error)  # noqa: TRY400 - the reason is enough
        raise
    return 0
