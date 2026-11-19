"""The link to Orca: `helper.py` on this Linux machine, or on one asc reaches over ssh.

It is Orca's `Link` (`inputs/keyboard.py`), as the relay is NVDA's. asc sends
the helper's source over stdin (see `BOOTSTRAP`), so nothing is installed on the
other machine. Its stderr stays in memory: it is shown in an error, never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import shlex
import subprocess
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from agent_speech_check.daemon.speech_log import SpeechLog
from agent_speech_check.errors import (
    AscError,
    FileUnusable,
    LaunchFailed,
    OrcaBusy,
    OrcaHelpNotFound,
    OrcaNotConnected,
    OrcaNotWatchable,
    OrcaSetupNeeded,
    ProtocolError,
    RemoteCommandFailed,
    SshMissing,
    bad_field,
)
from agent_speech_check.inputs.keys import Gesture, Key, typed_gestures
from agent_speech_check.orca import helper
from agent_speech_check.system import paths, ssh
from agent_speech_check.validate import Fields

log = logging.getLogger(__name__)

# Reads the helper's length, then the helper, from stdin: no quotes or
# backslashes, so ssh.exe gets it on a Windows command line unchanged.
BOOTSTRAP = "import sys;i=sys.stdin.buffer;exec(i.read(int(i.readline())))"
# At import, so a frozen asc without the file fails at once, not at asc start.
_HELPER = resources.files("agent_speech_check.orca").joinpath("helper.py").read_bytes()
MAX_LINE = 1_000_000
# Past the helper's xdotool timeout, so its error arrives first.
ACK_TIMEOUT_S = helper.XDOTOOL_TIMEOUT_S + 5
STOP_TIMEOUT_S = 10.0

KEYSYMS = {
    "NVDA": "Insert",
    "Control": "Control_L",
    "Shift": "Shift_L",
    "Alt": "Alt_L",
    "Windows": "Super_L",
    "Tab": "Tab",
    "Enter": "Return",
    "Escape": "Escape",
    "Space": "space",
    "Backspace": "BackSpace",
    "Delete": "Delete",
    "Insert": "Insert",
    "Home": "Home",
    "End": "End",
    "PageUp": "Prior",
    "PageDown": "Next",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
    "CapsLock": "Caps_Lock",
    "NumLock": "Num_Lock",
    "ScrollLock": "Scroll_Lock",
    "PrintScreen": "Print",
    "Pause": "Pause",
    "Applications": "Menu",
    # Num Lock off, as for NVDA: Orca's desktop layout reviews with these.
    "Numpad0": "KP_Insert",
    "Numpad1": "KP_End",
    "Numpad2": "KP_Down",
    "Numpad3": "KP_Next",
    "Numpad4": "KP_Left",
    "Numpad5": "KP_Begin",
    "Numpad6": "KP_Right",
    "Numpad7": "KP_Home",
    "Numpad8": "KP_Up",
    "Numpad9": "KP_Prior",
    "NumpadDelete": "KP_Delete",
    "NumpadEnter": "KP_Enter",
    "NumpadPlus": "KP_Add",
    "NumpadMinus": "KP_Subtract",
    "NumpadMultiply": "KP_Multiply",
    "NumpadDivide": "KP_Divide",
    "Comma": "comma",
    "Period": "period",
    "Slash": "slash",
    "Backslash": "backslash",
    "Semicolon": "semicolon",
    "Apostrophe": "apostrophe",
    "LeftBracket": "bracketleft",
    "RightBracket": "bracketright",
    "Minus": "minus",
    "Equals": "equal",
    "Grave": "grave",
}


def keysym(key: Key) -> str:
    """The X keysym xdotool presses. A typed character (`typed_gestures`) is its
    Unicode keysym, which xdotool maps to a key even when the layout has none."""
    if key.vk == 0:
        return f"U{ord(key.name):04X}"
    if key.name in KEYSYMS:
        return KEYSYMS[key.name]
    return key.name.lower() if len(key.name) == 1 else key.name


def helper_command(destination: str | None) -> list[str]:
    # The machine's own python3, here as over ssh: a frozen asc has no Python to run it.
    command = ["python3", "-I", "-c", BOOTSTRAP]
    if destination is None:
        return command
    return [*ssh.command(destination, "-T"), shlex.join(command)]


def payload(start: dict[str, str]) -> bytes:
    """The helper, then its first line."""
    return b"%d\n" % len(_HELPER) + _HELPER + json.dumps(start).encode() + b"\n"


def help_pages(destination: str | None, lang: str) -> dict[str, str]:
    """Orca's command pages by path, from its help here or on `destination`."""
    if destination is None:
        try:
            return helper.help_pages(lang)
        except helper.Failed as failed:
            raise OrcaHelpNotFound(failed.reason) from None
    remote = shlex.join(helper_command(None))
    _, out, detail = ssh.run(destination, remote, payload({"help": lang}))
    message = parse_message(out.splitlines()[-1]) if out.strip() else None
    if isinstance(message, Help):
        return message.pages
    if isinstance(message, Failed):
        raise message.error
    raise RemoteCommandFailed(destination, "python3", detail)


def watch_secret_file() -> Path:
    # One per user, as Orca is: watch mode runs only in the default instance.
    return paths.base_root() / "orca-secret"


def watch_secret(path: Path | None = None) -> str:
    """The secret the user's own Orca must start with for watch mode, made once and kept."""
    path = path or watch_secret_file()
    try:
        if path.exists():
            kept = path.read_text(encoding="ascii", errors="replace").strip()
            if re.fullmatch(r"[0-9a-f]{32}", kept):
                return kept
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise FileUnusable.from_os(path, error) from None
    secret = secrets.token_hex(16)
    paths.write_atomic(path, secret)  # only this user may read it (mkstemp's 0600)
    return secret


def restart_command(path: Path) -> str:
    """The line that restarts the user's Orca with asc's secret, for them to run."""
    secret = f"$(cat {shlex.quote(str(path))})"
    return f"ORCA_TEST_RPC_SECRET={secret} setsid orca --replace >/dev/null 2>&1 &"


@dataclass(frozen=True)
class Ready:
    version: str


@dataclass(frozen=True)
class Record:
    kind: str
    text: str | None


@dataclass(frozen=True)
class Done:
    id: int
    error: str | None


@dataclass(frozen=True)
class Failed:
    error: AscError


@dataclass(frozen=True)
class Help:
    pages: dict[str, str]


type Message = Ready | Record | Done | Failed | Help


def parse_message(line: bytes) -> Message | None:
    """One line from the helper. None for a message this asc doesn't know."""
    where = "Orca's helper"
    try:
        raw = json.loads(line)
    except ValueError:
        shown = line.decode(errors="replace")
        raise bad_field(ProtocolError, where, "a JSON line", shown) from None
    f = Fields(raw, where, ProtocolError)
    if f.has("ready"):
        return Ready(f.text("ready", max_len=100))
    if f.has("record"):
        return _record(raw["record"])
    if f.has("done"):
        error = f.opt_text("error", max_len=MAX_LINE)
        return Done(f.number("done", low=1, high=2**53), None if error is None else error[:2000])
    if f.has("failed"):
        kind = f.choice("failed", ("setup", "busy", "watch", "help"))
        reason = f.text("reason", max_len=2000)
        failure: AscError
        if kind == "help":
            failure = OrcaHelpNotFound(reason)
        elif kind == "watch":
            failure = OrcaNotWatchable(reason, restart_command(watch_secret_file()))
        else:
            failure = OrcaSetupNeeded(reason) if kind == "setup" else OrcaBusy(reason)
        return Failed(failure)
    if f.has("help"):
        pages = Fields(raw["help"], f"{where} help", ProtocolError)
        return Help(
            {path: pages.text(path, max_len=MAX_LINE, allow_empty=True) for path in pages.raw}
        )
    return None


def _record(raw: object) -> Record | None:
    # Orca wrote it, not the helper: like NVDA's relay, skip what this asc can't read.
    try:
        record = Fields(raw, "Orca's helper record", ProtocolError)
        kind = record.text("kind", max_len=100)
        speech = kind == "speech"
        text = record.text("text", max_len=MAX_LINE, allow_empty=True) if speech else None
    except ProtocolError:
        log.debug("skipped a record of Orca's that this asc can't read")
        return None
    return Record(kind, text)


class OrcaLink:
    not_connected = OrcaNotConnected

    def __init__(
        self, destination: str | None, speech: SpeechLog, watch: str | None = None
    ) -> None:
        self.destination = destination
        self.speech = speech
        # Watch mode's secret: then the helper records the user's own Orca.
        self.watch = watch
        self.on_join: Callable[[], None] = lambda: None
        self.last_error: str | None = None
        # Why Orca never got ready, for `asc start` to raise.
        self.failure: AscError | None = None
        # As `Relay.losses`: one entry per lost connection, a protocol error's message or None.
        self.losses: list[str | None] = []
        self._process: asyncio.subprocess.Process | None = None
        self._stdin: asyncio.StreamWriter | None = None
        self._ready = False
        self._acks: dict[int, asyncio.Future[str | None]] = {}
        self._last_id = 0
        self._stderr: deque[str] = deque(maxlen=ssh.STDERR_LINES)
        self._drain: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self._ready

    def check(self, since: int, *, drop_fails: bool) -> None:  # noqa: ARG002
        """As `Relay.check`, but any loss fails: unlike NVDA, Orca never comes back."""
        new = self.losses[since:]
        for reason in new:
            if reason is not None:
                raise ProtocolError(reason)
        if new or not self.connected:
            raise OrcaNotConnected(last_error=self.last_error)

    def gestures(self, text: str) -> tuple[Gesture, ...]:
        """X names each character by its keysym."""
        return typed_gestures(text)

    def status_fields(self) -> dict[str, Any]:
        return {}

    async def run(self) -> None:
        """Start the helper and follow it until it ends. Never raises: what went
        wrong goes to `failure` and `last_error`, and the daemon keeps running."""
        command = helper_command(self.destination)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                limit=MAX_LINE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            detail = error.strerror or str(error)
            self._fail(
                SshMissing(detail)
                if self.destination
                else OrcaSetupNeeded(f"cannot run python: {detail}")
            )
            return
        stdin, stdout, stderr = _pipes(process)
        self._process, self._stdin = process, stdin
        self._drain = asyncio.create_task(self._keep_stderr(stderr))
        stdin.write(payload({} if self.watch is None else {"watch": self.watch}))
        reason = await self._follow(stdout)
        if reason is not None:
            await self.stop()
        code = await process.wait()
        await self._drain
        self._ended(code, reason)

    async def _follow(self, stdout: asyncio.StreamReader) -> ProtocolError | None:
        while True:
            try:
                line = await stdout.readline()
            except ValueError:
                return ProtocolError(f"Orca's helper sent a line over {MAX_LINE} bytes")
            if not line:
                return None
            try:
                message = parse_message(line)
            except ProtocolError as error:
                return error
            self._dispatch(message)

    def _dispatch(self, message: Message | None) -> None:
        match message:
            case Ready(version=version):
                self._ready = True
                self.last_error = None
                log.info("Orca %s ready", version)
                self.on_join()
            case Record(kind="speech", text=str(text)):
                log.debug("speak, %d characters", len(text))
                self.speech.said(text)
            case Record():
                self.speech.activity()
            case Done(id=ident, error=error):
                future = self._acks.pop(ident, None)
                if future is not None and not future.done():
                    future.set_result(error)
            case Failed(error=error):
                self._fail(error)
            case Help() | None:
                pass

    def _fail(self, error: AscError) -> None:
        self.failure = error
        self.last_error = error.message
        log.warning("Orca failed: %s", type(error).__name__)

    def _lose(self, error: str) -> None:
        """The link is gone, though the helper may still run: `_ended` follows."""
        self._ready = False
        self.losses.append(None)
        self.last_error = error

    def _ended(self, code: int, reason: ProtocolError | None) -> None:
        was_ready, self._ready = self._ready, False
        for future in self._acks.values():
            if not future.done():
                future.set_exception(OrcaNotConnected())
        self._acks.clear()
        log.info("Orca's helper exited with code %d", code)
        if reason is not None:
            self.last_error = reason.message
        if was_ready:
            self.losses.append(None if reason is None else reason.message)
            self.last_error = self.last_error or f"Orca stopped (exit code {code})"
        elif self.failure is None and not self.losses:
            self._fail(reason or self._exit_error(code))
        self.speech.activity()

    def _exit_error(self, code: int) -> AscError:
        if self.destination is not None and code == ssh.FAILED:
            return ssh.failed(self.destination, self._stderr, code)
        detail = self._stderr[-1] if self._stderr else f"exit code {code}"
        return OrcaSetupNeeded(f"its helper exited with code {code}: {detail}")

    async def _keep_stderr(self, stderr: asyncio.StreamReader) -> None:
        async for line in ssh.stderr_lines(stderr):
            self._stderr.append(line)

    def _write(self, message: dict[str, Any]) -> None:
        stdin = self._stdin
        if not self._ready or stdin is None:
            raise OrcaNotConnected
        stdin.write(json.dumps(message).encode() + b"\n")

    async def _ask(self, message: dict[str, Any]) -> str | None:
        """Send a message and wait for its `done`: its error, or None."""
        self._last_id += 1
        ident = self._last_id
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._write({**message, "id": ident})
        self._acks[ident] = future
        try:
            return await asyncio.wait_for(future, ACK_TIMEOUT_S)
        except TimeoutError:
            if self._ready:  # another wait may have found it lost already
                self._lose(f"Orca's helper did not answer within {ACK_TIMEOUT_S:g} s")
                await self.stop()
            raise OrcaNotConnected(last_error=self.last_error) from None
        finally:
            self._acks.pop(ident, None)

    def write_key(self, key: Key, *, pressed: bool) -> None:
        """Queue one key event without waiting. Only `keyboard.Keyboard` sends keys."""
        self._write({"key": keysym(key), "down": pressed})

    async def send_key(self, key: Key, *, pressed: bool) -> None:
        """Send one key event and wait until xdotool has pressed it."""
        name = keysym(key)
        error = await self._ask({"key": name, "down": pressed})
        if error is not None:
            reason = f"xdotool could not press {name}: {error}"
            raise ProtocolError(reason)

    async def launch(self, command: str) -> None:
        error = await self._ask({"launch": command})
        if error is not None:
            raise LaunchFailed(command, error)

    async def stop(self) -> None:
        """End the helper, which ends Orca and all it started. Its stdin closing
        tells it to, or SIGTERM while it sets up and reads no stdin; past that it
        is killed (ssh: the remote end hangs up)."""
        process, stdin = self._process, self._stdin
        if process is None or stdin is None or process.returncode is not None:
            return
        with contextlib.suppress(OSError):
            await stdin.drain()
        stdin.close()
        for end in (process.terminate, process.kill):
            try:
                await asyncio.wait_for(process.wait(), STOP_TIMEOUT_S)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    end()
            else:
                return
        await process.wait()


def _pipes(
    process: asyncio.subprocess.Process,
) -> tuple[asyncio.StreamWriter, asyncio.StreamReader, asyncio.StreamReader]:
    """Its stdin, stdout and stderr, which are None only when not piped."""
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError
    return process.stdin, process.stdout, process.stderr
