"""The NVDA Remote Access wire format, as observed in `docs/protocol.md`.

Pure functions, no I/O: `parse_message` turns one received line into a frozen
dataclass, and the `encode_*` functions build the lines `asc` sends. `asc`
is the relay and the leader, and NVDA is the follower.

An unknown `type` becomes `Unknown`, so a newer NVDA doesn't break `asc`.
Anything else that doesn't fit raises `ProtocolError`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_speech_check.errors import LineTooLong, MalformedLine, ProtocolError, ServerSentError
from agent_speech_check.validate import Fields

MAX_LINE = 1024 * 1024
LEADER_ID = 1


@dataclass(frozen=True)
class ProtocolVersion:
    version: int


@dataclass(frozen=True)
class Join:
    channel: str
    connection_type: str


@dataclass(frozen=True)
class Speak:
    """One utterance. `text` is the spoken strings, stripped and joined by spaces."""

    text: str
    priority: int


@dataclass(frozen=True)
class Cancel:
    pass


@dataclass(frozen=True)
class Ping:
    pass


@dataclass(frozen=True)
class Wave:
    """A sound NVDA played. `name` is the file's base name, such as `focusMode.wav`."""

    name: str


@dataclass(frozen=True)
class Tone:
    hz: float
    length: int


@dataclass(frozen=True)
class PauseSpeech:
    paused: bool


@dataclass(frozen=True)
class Unknown:
    type: str


type Message = ProtocolVersion | Join | Speak | Cancel | Ping | Wave | Tone | PauseSpeech | Unknown


def _speak(f: Fields) -> Speak:
    strings = [item.strip() for item in f.items("sequence") if isinstance(item, str)]
    text = " ".join(s for s in strings if s)
    priority = f.opt_number("priority", low=0, high=1000)
    return Speak(text, priority or 0)


def _wave(f: Fields) -> Wave:
    path = f.text("fileName", max_len=4096)
    # A Windows path from NVDA's machine: keep only the base name.
    return Wave(path.replace("/", "\\").rsplit("\\", 1)[-1])


def _tone(f: Fields) -> Tone:
    return Tone(f.real("hz"), f.number("length", low=0, high=600_000))


def _error(f: Fields) -> Message:
    raise ServerSentError(f.opt_text("message") or "(no message)")


_PARSERS: dict[str, Callable[[Fields], Message]] = {
    "protocol_version": lambda f: ProtocolVersion(f.number("version", low=0, high=1000)),
    "join": lambda f: Join(f.text("channel", max_len=1024), f.text("connection_type")),
    "speak": _speak,
    "cancel": lambda _f: Cancel(),
    "ping": lambda _f: Ping(),
    "wave": _wave,
    "tone": _tone,
    "pause_speech": lambda f: PauseSpeech(f.flag("switch")),
    "error": _error,
}


def parse_message(line: bytes) -> Message:
    """Parse one line received from NVDA, without its trailing newline."""
    if len(line) > MAX_LINE:
        raise LineTooLong(MAX_LINE)
    try:
        raw = json.loads(line)
    except (ValueError, UnicodeDecodeError) as error:
        raise MalformedLine(str(error)) from None
    kind = Fields(raw, "NVDA message", ProtocolError).text("type", max_len=100)
    parser = _PARSERS.get(kind)
    if parser is None:
        return Unknown(kind)
    message: Message = parser(Fields(raw, f"NVDA {kind} message", ProtocolError))
    return message


def _line(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def _leader() -> dict[str, Any]:
    return {"id": LEADER_ID, "connection_type": "master"}


def encode_channel_joined(channel: str) -> bytes:
    """The relay's reply to a follower's `join`. It must list a leader, or NVDA sends no speech."""
    return _line(
        {
            "type": "channel_joined",
            "channel": channel,
            "user_ids": [LEADER_ID],
            "clients": [_leader()],
        }
    )


def encode_key(vk_code: int, *, extended: bool, pressed: bool) -> bytes:
    return _line({"type": "key", "vk_code": vk_code, "extended": extended, "pressed": pressed})


def encode_ping() -> bytes:
    return _line({"type": "ping"})


def encode_error(message: str) -> bytes:
    return _line({"type": "error", "message": message})
