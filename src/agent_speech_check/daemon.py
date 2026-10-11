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

from agent_speech_check import firewall, ipc, nvda, nvda_config, relay, state, winapi
from agent_speech_check.errors import (
    AlreadyRunning,
    LaunchFailed,
    NoMatch,
    NvdaNotConnected,
    PanicKeyTaken,
    SharedNeedsConsent,
)
from agent_speech_check.keyboard import Keyboard
from agent_speech_check.paths import state_root, target_dir
from agent_speech_check.relay import Relay
from agent_speech_check.sandboxed import SandboxSession
from agent_speech_check.speech_log import SpeechLog
from agent_speech_check.targets import Target

log = logging.getLogger(__name__)

LOG_FILE = "daemon.log"
# Shared mode's panic hotkey: releases held keys and stops asc.
PANIC_KEY = "Control+Shift+Alt+F12"
PANIC_MODIFIERS = winapi.MOD_CONTROL | winapi.MOD_SHIFT | winapi.MOD_ALT
PANIC_VK = 0x7B
STOP_DELAY_S = 0.05


def _setup_logging(folder: Path) -> None:
    handler = RotatingFileHandler(
        folder / LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


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


class Daemon:
    def __init__(self, target: Target, folder: Path) -> None:
        self.target = target
        self.folder = folder
        self.speech = SpeechLog()
        self.relay = Relay(target.name, relay.load_or_create(folder), self.speech)
        self.keyboard = Keyboard(target, self.relay, self.speech)
        self.stopping = asyncio.Event()
        self.nvda: nvda.Running | None = None
        self.sandbox: SandboxSession | None = None
        self.panic: winapi.GlobalHotkey | None = None

    async def handle(self, request: ipc.Request) -> dict[str, Any]:
        handlers: dict[str, Callable[[Any], Awaitable[dict[str, Any]]]] = {
            "status": self._status,
            "stop": self._stop,
            "mark": self._mark,
            "clear": self._clear,
            "speech": self._speech,
            "wait": self._wait,
            "press": self._press,
            "type": self._type,
            "launch": self._launch,
        }
        return await handlers[request.cmd](request.args)

    async def _status(self, _args: ipc.NoArgs) -> dict[str, Any]:
        return self.status()

    async def _stop(self, _args: ipc.NoArgs) -> dict[str, Any]:
        asyncio.get_running_loop().call_later(STOP_DELAY_S, self.stopping.set)
        return {}

    async def _mark(self, _args: ipc.NoArgs) -> dict[str, Any]:
        return {"mark": self.speech.set_mark()}

    async def _clear(self, _args: ipc.NoArgs) -> dict[str, Any]:
        self.speech.clear()
        return {}

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
            "panic_key": None if self.panic is None else PANIC_KEY,
        }

    async def _wait(self, args: ipc.WaitArgs) -> dict[str, Any]:
        if not self.relay.connected:
            raise NvdaNotConnected(self.target.name)
        after = self.speech.mark if args.since is None else args.since
        failures = self.relay.failures
        entry = await self.speech.wait_match(
            args.regex,
            after,
            args.timeout_ms / 1000,
            lambda: self.relay.raise_failure_since(failures),
        )
        if entry is None:
            said = [e.text for e in self.speech.since(after).entries if e.kind == "said"]
            raise NoMatch(args.pattern, args.timeout_ms, said)
        return {"line": [entry.n, entry.line()]}

    async def _press(self, args: ipc.PressArgs) -> dict[str, Any]:
        pressed = await self.keyboard.press(
            args.parsed, args.settle_ms / 1000, args.timeout_ms / 1000, args.regex
        )
        if not pressed.matched:
            said = [e.text for e in pressed.entries if e.kind == "said"]
            raise NoMatch(args.expect or "", args.timeout_ms, said)
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
        if self.sandbox is not None:
            await self.sandbox.launch(args.command)
        else:
            start_app(args.command)
        timeout_s = args.timeout_ms / 1000
        title = args.regex
        if title is None:
            # No title given: wait for the first speech, then for quiet.
            title = re.compile(".")
        found = await self.speech.wait_match(title, start, timeout_s)
        if found is None:
            said = [e.text for e in self.speech.since(start).entries if e.kind == "said"]
            raise NoMatch(args.title or ".", args.timeout_ms, said)
        await self.speech.wait_quiet(ipc.DEFAULT_SETTLE_MS / 1000, timeout_s)
        return {"lines": [e.line() for e in self.speech.since(start).entries]}

    def _relay_info(self) -> nvda_config.Relay:
        credentials = self.relay.credentials
        return nvda_config.Relay(
            self.relay.host, self.relay.port, credentials.channel, credentials.fingerprint
        )

    def _start_nvda(self) -> None:
        if self.target.mode == "desktop" and self.target.launch:
            self.nvda = nvda.launch(
                nvda.folder_for(self.target.nvda_dir),
                self.folder / "nvda_config",
                self._relay_info(),
                self.target.settings,
            )

    def _start_panic_key(self) -> None:
        """Shared mode never runs without a way for the user to stop it at once."""
        if self.target.mode != "shared":
            return
        if not self.target.consent:
            raise SharedNeedsConsent
        loop = asyncio.get_running_loop()

        def pressed() -> None:
            loop.call_soon_threadsafe(self._on_panic)

        hotkey = winapi.GlobalHotkey(PANIC_MODIFIERS, PANIC_VK, pressed)
        if not hotkey.start():
            raise PanicKeyTaken(PANIC_KEY)
        self.panic = hotkey

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
            self._start_nvda()
            state.State(os.getpid(), port, token, time.time()).write(self.folder)
            async with asyncio.TaskGroup() as group:
                keepalive = group.create_task(self.relay.keepalive())
                if self.target.mode == "sandbox" and self.target.launch:
                    self.sandbox = SandboxSession(
                        self.folder, self.target.share, self._relay_info(), self.target.settings
                    )
                    group.create_task(self.sandbox.run(self.stopping))
                await self.stopping.wait()
                keepalive.cancel()
        finally:
            await self.keyboard.release_all()
            if self.nvda is not None:
                self.nvda.stop()
            if self.sandbox is not None:
                await self.sandbox.stop()
            self.relay.disconnect()
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
    if winapi.take_mutex(winapi.mutex_name(state_root(), target.name)) is None:
        raise AlreadyRunning(target.name)
    log.info("starting %s (%s mode)", target.name, target.mode)
    try:
        asyncio.run(_main(target, folder))
    except OSError as error:
        log.error("could not start: %s", error)  # noqa: TRY400 - the reason is enough
        raise
    return 0
