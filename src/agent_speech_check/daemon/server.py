"""The background process for one target: relay, speech log, keys and waits.

`asc start` runs it as `asc _daemon <target json>`. It writes the state file
once it listens, runs until `asc stop` (or Ctrl+Break), then releases any held
keys and removes the state file. If a long-lived task crashes, it logs the
error and exits rather than keep running half dead.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import secrets
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from agent_speech_check.config.targets import Target
from agent_speech_check.daemon import ipc, state
from agent_speech_check.errors import (
    AlreadyRunning,
    AscError,
    LaunchFailed,
    NoMatch,
    NvdaNotConnected,
    PanicKeyTaken,
    SharedNeedsConsent,
)
from agent_speech_check.inputs import mouse
from agent_speech_check.inputs.keyboard import Keyboard
from agent_speech_check.inputs.keys import Hotkey
from agent_speech_check.nvda import launcher, nvda_config
from agent_speech_check.remote import relay
from agent_speech_check.remote.relay import Relay
from agent_speech_check.remote.speech_log import Entry, SpeechLog
from agent_speech_check.sandbox import firewall
from agent_speech_check.sandbox.sandboxed import SandboxSession
from agent_speech_check.windows import winapi
from agent_speech_check.windows.paths import state_root, target_dir

log = logging.getLogger(__name__)

LOG_FILE = "daemon.log"
STOP_DELAY_S = 0.05


def _setup_logging(folder: Path) -> None:
    handler = RotatingFileHandler(
        folder / LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def take_panic_key(key: Hotkey, on_press: Callable[[], None]) -> winapi.GlobalHotkey:
    """asc never sends keys into this Windows session without a way for the user to
    stop it at once: the panic hotkey releases held keys and stops asc."""
    hotkey = winapi.GlobalHotkey(key.modifiers, key.vk, on_press)
    if not hotkey.start():
        raise PanicKeyTaken(key.text)
    return hotkey


def check_panic_key(key: Hotkey) -> None:
    """Refuse to start up front if another program owns the panic hotkey."""
    take_panic_key(key, lambda: None).stop()


def start_app(command: str) -> None:
    """Start the app under test, detached. Popen returns at once."""
    try:
        subprocess.Popen(  # noqa: S603 - the user's own app, run as asked
            command,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
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


def _said(entries: tuple[Entry, ...]) -> list[str]:
    return [e.text for e in entries if e.kind == "said"]


class Daemon:
    def __init__(self, target: Target, folder: Path) -> None:
        self.target = target
        self.folder = folder
        self.speech = SpeechLog()
        self.relay = Relay(target.name, relay.load_or_create(folder), self.speech)
        self.keyboard = Keyboard(target, self.relay, self.speech)
        self.stopping = asyncio.Event()
        self.nvda: launcher.Running | None = None
        self.sandbox: SandboxSession | None = None
        self.panic: winapi.GlobalHotkey | None = None
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
        return {"mark": self.speech.set_mark()}

    async def _speech(self, args: ipc.SpeechArgs) -> dict[str, Any]:
        result = self.speech.since(self.speech.mark if args.since is None else args.since)
        return {"lines": [[e.n, e.line()] for e in result.entries], "dropped": result.dropped}

    def status(self) -> dict[str, Any]:
        return {
            "target": self.target.name,
            "mode": self.target.mode,
            "keys": self.target.keys,
            "connected": self.relay.connected,
            "host": f"{self.relay.host}:{self.relay.port}",
            "channel": self.relay.credentials.channel,
            "fingerprint": self.relay.credentials.fingerprint,
            "nvda_mode": self.speech.mode,
            "lines": len(self.speech),
            "latest": self.speech.latest,
            "mark": self.speech.mark,
            "last_error": self.relay.last_error,
            "nvda_pid": None if self.nvda is None else self.nvda.process.pid,
            "sandbox": None if self.sandbox is None else self.sandbox.state,
            "sandbox_failure": self._sandbox_failure(),
            "panic_key": None if self.panic is None else self.target.panic_key.text,
            "running": list(self.running),
        }

    def _sandbox_failure(self) -> dict[str, Any] | None:
        failure = None if self.sandbox is None else self.sandbox.failure
        return None if failure is None else ipc.error_detail(failure)

    async def _wait(self, args: ipc.WaitArgs) -> dict[str, Any]:
        if not self.relay.connected:
            raise NvdaNotConnected(self.target.name)
        after = self.speech.mark if args.since is None else args.since
        losses_before = len(self.relay.losses)
        entry = await self._wait_match(args.regex, after, args.timeout_ms, losses_before)
        if entry is None:
            raise self._no_match(args.pattern, args.timeout_ms, after)
        return {"line": [entry.n, entry.line()]}

    async def _wait_match(
        self, pattern: re.Pattern[str], after: int, timeout_ms: int, losses_before: int
    ) -> Entry | None:
        """Like `SpeechLog.wait_match`, but fails on a protocol error from NVDA
        after `losses_before` (`len(relay.losses)` when the command began).

        A plain drop doesn't fail it: NVDA reconnects by itself about 5 s later.
        """
        check = functools.partial(self.relay.check, losses_before, drop_fails=False)
        return await self.speech.wait_match(pattern, after, timeout_ms / 1000, check)

    def _no_match(
        self, pattern: str, timeout_ms: int, after: int, windows: list[str] | None = None
    ) -> AscError:
        """Nothing matched in time: say what NVDA said, or that it went away and stayed away."""
        if not self.relay.connected:
            return NvdaNotConnected(self.target.name, timeout_ms, self.relay.last_error)
        return NoMatch(pattern, timeout_ms, _said(self.speech.since(after).entries), windows)

    async def _press(self, args: ipc.PressArgs) -> dict[str, Any]:
        pressed = await self.keyboard.press(
            args.parsed, args.settle_ms / 1000, args.timeout_ms / 1000, args.regex
        )
        if not pressed.matched:
            # A drop fails a press before this, so NVDA is still connected here.
            raise NoMatch(args.expect or "", args.timeout_ms, _said(pressed.entries))
        return {"lines": [e.line() for e in pressed.entries]}

    async def _type(self, args: ipc.TypeArgs) -> dict[str, Any]:
        typed = await self.keyboard.press(
            args.parsed, args.settle_ms / 1000, args.timeout_ms / 1000, settle_each=False
        )
        return {"lines": [e.line() for e in typed.entries]}

    async def _launch(self, args: ipc.LaunchArgs) -> dict[str, Any]:
        if not self.relay.connected:
            raise NvdaNotConnected(self.target.name)
        start = self.speech.latest
        # Before the launch, which can take seconds in a sandbox.
        losses_before = len(self.relay.losses)
        # The sandbox's windows can't be seen from here.
        before = None if self.sandbox is not None else set(winapi.windows_titled(""))
        if self.sandbox is not None:
            await self.sandbox.launch(args.command)
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
        return {"lines": [e.line() for e in self.speech.since(start).entries]}

    def _relay_info(self) -> nvda_config.RelayAddress:
        credentials = self.relay.credentials
        return nvda_config.RelayAddress(
            self.relay.host, self.relay.port, credentials.channel, credentials.fingerprint
        )

    def _start_nvda(self) -> None:
        if self.target.launches_portable:
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

    async def run(self) -> None:
        self._start_panic_key()
        host = relay.HOST
        if self.target.mode == "sandbox":
            # NVDA in the sandbox reaches the host only on the Default Switch.
            host = await asyncio.to_thread(firewall.default_switch_address)
        relay_server = await self.relay.start(self.target.port, host)
        token = secrets.token_urlsafe(32)
        ipc_server = await asyncio.start_server(
            functools.partial(ipc.serve_one, token, self.handle),
            "127.0.0.1",
            0,
            limit=ipc.MAX_REQUEST,
        )
        port = ipc_server.sockets[0].getsockname()[1]
        log.info("relay on %s, IPC on %d", self.relay.port, port)
        try:
            # Off the loop: copying the user's config (add-ons too) can take a while.
            await asyncio.to_thread(self._start_nvda)
            state.State(os.getpid(), port, token, time.time()).write(self.folder)
            async with asyncio.TaskGroup() as group:
                keepalive = group.create_task(self.relay.keepalive())
                if self.target.launches_sandbox:
                    self.sandbox = SandboxSession(self.folder, self.target, self._relay_info())
                    group.create_task(self.sandbox.run())
                await self.stopping.wait()
                keepalive.cancel()
        finally:
            self.keyboard.release_all()
            # Before NVDA goes, so it can still answer the close.
            self.relay.disconnect()
            if self.nvda is not None:
                self.nvda.stop()
            if self.sandbox is not None:
                await self.sandbox.stop()
            relay_server.close()
            ipc_server.close()
            state.remove(self.folder)
            if self.panic is not None:
                self.panic.stop()
            log.info("stopped")


def _on_break(loop: asyncio.AbstractEventLoop, daemon: Daemon) -> None:
    loop.call_soon_threadsafe(daemon.stopping.set)


async def _main(target: Target, folder: Path) -> None:
    daemon = Daemon(target, folder)
    loop = asyncio.get_running_loop()
    signal.signal(signal.SIGBREAK, lambda *_: _on_break(loop, daemon))
    await daemon.run()


def run(target_json: str) -> int:
    """Entry point for `asc _daemon`. Returns the process exit code."""
    target = Target.from_json(target_json)
    folder = target_dir(target.name)
    folder.mkdir(parents=True, exist_ok=True)
    _setup_logging(folder)
    if winapi.take_mutex(winapi.mutex_name(state_root())) is None:
        raise AlreadyRunning
    log.info("starting %s (%s mode)", target.name, target.mode)
    try:
        asyncio.run(_main(target, folder))
    except OSError as error:
        log.error("could not start: %s", error)  # noqa: TRY400 - the reason is enough
        raise
    return 0
