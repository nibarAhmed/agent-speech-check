"""CLI to daemon: JSON lines over 127.0.0.1 TCP, one request per connection.

Request:  {"v": 1, "token": "...", "cmd": "press", "args": {...}}
Response: {"v": 1, "ok": true, "result": {...}}
       or {"v": 1, "ok": false, "error": {"kind": "refused", "message": "...", "hint": "..."}}

The CLI and the daemon parse requests with the same `parse_request`: the CLI for
a fast, friendly error, the daemon because it is the authority. A wrong token
closes the connection without a reply.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_speech_check.daemon import state
from agent_speech_check.errors import (
    IPC_VERSION,
    AscError,
    BadRegex,
    DaemonHungUp,
    DaemonNoReply,
    DaemonUnreachable,
    DaemonVersionMismatch,
    InternalError,
    NotRunning,
    UsageError,
    bad_field,
    from_kind,
)
from agent_speech_check.inputs.keys import Gesture, parse_gesture, text_gestures
from agent_speech_check.validate import Fields

log = logging.getLogger(__name__)

MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 64 * 1024 * 1024
MAX_TIMEOUT_MS = 600_000
SETTLE_MS = (50, 10_000)
DEFAULT_SETTLE_MS = 400
DEFAULT_TIMEOUT_MS = 5_000
CONNECT_TIMEOUT_S = 3.0
MAX_GESTURES = 100
MAX_TYPED = 2000
DEFAULT_LAUNCH_TIMEOUT_MS = 30_000
# How long starting the app may take before --timeout starts counting. In
# sandbox mode the launch script alone waits up to 30 s for a new window.
LAUNCH_START_MS = 60_000


def compile_regex(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as error:
        raise BadRegex(pattern, error.msg, error.pos) from None


@dataclass(frozen=True)
class NoArgs:
    @classmethod
    def parse(cls, _f: Fields) -> NoArgs:
        return cls()


def _timeout(f: Fields) -> int:
    return f.number("timeout_ms", low=1, high=MAX_TIMEOUT_MS)


def _settle(f: Fields) -> int:
    return f.number("settle_ms", low=SETTLE_MS[0], high=SETTLE_MS[1])


def _since(f: Fields) -> int | None:
    return f.opt_number("since", low=0, high=2**53)


@dataclass(frozen=True)
class SpeechArgs:
    since: int | None

    @classmethod
    def parse(cls, f: Fields) -> SpeechArgs:
        return cls(_since(f))


@dataclass(frozen=True)
class WaitArgs:
    pattern: str
    timeout_ms: int
    since: int | None

    @property
    def regex(self) -> re.Pattern[str]:
        return compile_regex(self.pattern)

    @classmethod
    def parse(cls, f: Fields) -> WaitArgs:
        args = cls(
            f.text("pattern", max_len=2000),
            _timeout(f),
            _since(f),
        )
        compile_regex(args.pattern)
        return args


@dataclass(frozen=True)
class PressArgs:
    gestures: tuple[str, ...]
    expect: str | None
    settle_ms: int
    timeout_ms: int

    @property
    def parsed(self) -> tuple[Gesture, ...]:
        return tuple(parse_gesture(text) for text in self.gestures)

    @property
    def regex(self) -> re.Pattern[str] | None:
        return None if self.expect is None else compile_regex(self.expect)

    @classmethod
    def parse(cls, f: Fields) -> PressArgs:
        raw = f.items("gestures")
        if not raw or len(raw) > MAX_GESTURES or not all(isinstance(g, str) and g for g in raw):
            raise bad_field(
                UsageError, f"{f.where} gestures", f"1 to {MAX_GESTURES} key names", raw
            )
        args = cls(
            tuple(raw),
            f.opt_text("expect", max_len=2000),
            _settle(f),
            _timeout(f),
        )
        # Check everything before anything is sent.
        args.parsed  # noqa: B018 - parsing is the check
        args.regex  # noqa: B018 - compiling is the check
        return args


@dataclass(frozen=True)
class TypeArgs:
    text: str
    settle_ms: int
    timeout_ms: int

    @property
    def parsed(self) -> tuple[Gesture, ...]:
        return text_gestures(self.text)

    @classmethod
    def parse(cls, f: Fields) -> TypeArgs:
        args = cls(
            f.text("text", max_len=MAX_TYPED),
            _settle(f),
            _timeout(f),
        )
        args.parsed  # noqa: B018 - every character is checked before any is typed
        return args


@dataclass(frozen=True)
class LaunchArgs:
    command: str
    title: str | None
    timeout_ms: int

    @property
    def regex(self) -> re.Pattern[str] | None:
        return None if self.title is None else compile_regex(self.title)

    @classmethod
    def parse(cls, f: Fields) -> LaunchArgs:
        args = cls(
            f.text("command", max_len=8000),
            f.opt_text("title", max_len=2000),
            _timeout(f),
        )
        args.regex  # noqa: B018 - compiling is the check
        return args


type Args = NoArgs | SpeechArgs | WaitArgs | PressArgs | TypeArgs | LaunchArgs

COMMANDS: dict[str, Callable[[Fields], Args]] = {
    "status": NoArgs.parse,
    "stop": NoArgs.parse,
    "mark": NoArgs.parse,
    "speech": SpeechArgs.parse,
    "wait": WaitArgs.parse,
    "press": PressArgs.parse,
    "type": TypeArgs.parse,
    "launch": LaunchArgs.parse,
}


@dataclass(frozen=True)
class Request:
    cmd: str
    args: Args


def parse_request(cmd: str, raw_args: object) -> Request:
    f = Fields({"cmd": cmd}, "request", UsageError)
    name = f.choice("cmd", COMMANDS)
    args = COMMANDS[name](Fields(raw_args, f"{name} request", UsageError))
    return Request(name, args)


# ---------------------------------------------------------------- client


def _exchange(port: int, payload: bytes, timeout_s: float) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT_S) as sock:
        sock.settimeout(timeout_s)
        sock.sendall(payload)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if chunk.endswith(b"\n") or size > MAX_RESPONSE:
                break
        return b"".join(chunks)


def call(folder: Path, request: Request, timeout_s: float) -> dict[str, Any]:
    """Send one request to the daemon whose state file is in `folder`, and return its result."""
    current = state.read(folder)
    message = {
        "v": IPC_VERSION,
        "token": current.token,
        "cmd": request.cmd,
        "args": asdict(request.args),
    }
    payload = json.dumps(message).encode() + b"\n"
    try:
        data = _exchange(current.port, payload, timeout_s)
    except ConnectionRefusedError:
        state.remove(folder)
        raise NotRunning from None
    except TimeoutError:
        raise DaemonNoReply from None
    except OSError as error:
        raise DaemonUnreachable(str(error)) from None
    if not data.strip():
        raise DaemonHungUp
    return _parse_response(data)


def _parse_response(data: bytes) -> dict[str, Any]:
    try:
        raw = json.loads(data)
    except ValueError:
        raise bad_field(InternalError, "daemon response", "JSON", data[:80]) from None
    f = Fields(raw, "daemon response", InternalError)
    if f.raw.get("v") != IPC_VERSION:
        raise DaemonVersionMismatch(f.raw.get("v"))
    if f.flag("ok"):
        result: dict[str, Any] = dict(
            Fields(f.raw.get("result"), "daemon result", InternalError).raw
        )
        return result
    raise error_from(f.raw.get("error"), "daemon error")


def error_detail(error: AscError) -> dict[str, Any]:
    """An error as the daemon sends it, so `error_from` can rebuild it."""
    return {"kind": error.kind, "message": error.message, "hint": error.hint}


def error_from(raw: object, where: str) -> AscError:
    error = Fields(raw, where, InternalError)
    return from_kind(error.text("kind"), error.text("message"), error.opt_text("hint"))


# ---------------------------------------------------------------- server

type Handler = Callable[[Request], Awaitable[dict[str, Any]]]


def _reply(body: dict[str, Any]) -> bytes:
    return json.dumps({"v": IPC_VERSION, **body}).encode() + b"\n"


def _error_reply(error: AscError) -> bytes:
    return _reply({"ok": False, "error": error_detail(error)})


async def serve_one(
    token: str, handler: Handler, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Handle one connection: one request, one reply."""
    try:
        line = await reader.readuntil(b"\n")
        raw = json.loads(line)
        if not isinstance(raw, dict) or not hmac.compare_digest(
            str(raw.get("token", "")).encode(), token.encode()
        ):
            return
        writer.write(await _answer(raw, handler))
        await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
        log.info("dropped a bad IPC request")
    except ConnectionError:
        log.info("IPC client went away")
    finally:
        writer.close()


async def _answer(raw: dict[str, Any], handler: Handler) -> bytes:
    if raw.get("v") != IPC_VERSION:
        return _error_reply(DaemonVersionMismatch(raw.get("v")))
    try:
        cmd = raw.get("cmd")
        request = parse_request(cmd if isinstance(cmd, str) else "", raw.get("args"))
        return _reply({"ok": True, "result": await handler(request)})
    except AscError as error:
        return _error_reply(error)
    except Exception as error:
        # The daemon's log formatter keeps the bug's message, which can hold the
        # request's text, out of the log.
        log.exception("internal error handling %r", raw.get("cmd"))
        return _error_reply(InternalError(f"{type(error).__name__}: {error}"))
