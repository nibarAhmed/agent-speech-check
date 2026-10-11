"""A follower NVDA for tests: the real Remote Access protocol over real TLS.

It does what NVDA 2026.2 was observed doing in `docs/protocol.md`:
1. a verified TLS connect, which fails on the relay's self-signed cert,
2. a connect that only fetches the cert, then closes,
3. a connect pinned to the fingerprint, then `protocol_version` and `join`.

After `channel_joined` it answers key gestures from a script, and it records
which keys are held so tests can assert that none are left down. It also
scripts faults: a malformed line, an oversize line, an unknown type, a server
`error`, a disconnect mid-gesture, and silence.

Change this file only together with `docs/protocol.md`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Our own small vk table, independent of asc's keys module.
VK_NAMES = {
    0x2D: "NVDA",
    0x11: "Control",
    0x10: "Shift",
    0x12: "Alt",
    0x09: "Tab",
    0x1B: "Escape",
    0x20: "Space",
    0x26: "Up",
    0x28: "Down",
    0x48: "H",
    0x49: "I",
    0x54: "T",
}
MODIFIER_VKS = {0x2D, 0x11, 0x10, 0x12}

type Reply = list[dict[str, Any]]


def speak(*strings: str) -> dict[str, Any]:
    return {"type": "speak", "sequence": [*strings, ["EndUtteranceCommand", {}]], "priority": 0}


def wave(name: str) -> dict[str, Any]:
    return {
        "type": "wave",
        "fileName": f"C:\\nvda\\waves\\{name}",
        "asynchronous": True,
        "isSpeechWaveFileCommand": False,
    }


CANCEL: dict[str, Any] = {"type": "cancel"}

# Faults, used as a gesture's reply.
MALFORMED = "malformed"
OVERSIZE = "oversize"
UNKNOWN = "unknown"
SERVER_ERROR = "server_error"
DISCONNECT_MID_GESTURE = "disconnect_mid_gesture"
SILENCE = "silence"


@dataclass
class FakeNvda:
    port: int
    channel: str
    fingerprint: str
    replies: dict[str, Reply | str] = field(default_factory=dict)
    held: set[int] = field(default_factory=set)
    gestures: list[str] = field(default_factory=list)
    received: list[dict[str, Any]] = field(default_factory=list)
    joins: int = 0
    failure: AssertionError | None = None
    _sock: ssl.SSLSocket | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _down: list[int] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _joined: threading.Event = field(default_factory=threading.Event)

    # ------------------------------------------------------------ lifecycle

    def start(self, timeout_s: float = 10) -> FakeNvda:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.wait_joined(timeout_s)
        return self

    def wait_joined(self, timeout_s: float = 10) -> None:
        if not self._joined.wait(timeout_s):
            raise AssertionError(f"fake NVDA did not join: {self.failure}")

    def close(self) -> None:
        self._stop.set()
        self._drop()
        if self._thread is not None:
            self._thread.join(5)

    def _drop(self) -> None:
        sock, self._sock = self._sock, None
        self._joined.clear()
        # Keys stay held across a drop (observed in real NVDA); only the
        # gesture being built is forgotten.
        self._down.clear()
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect_like_nvda()
                self._read_loop()
            except (OSError, ValueError):
                pass
            except AssertionError as failure:
                self.failure = failure
                self._stop.set()
            self._drop()
            # Real NVDA reconnects about 5 s later; tests don't need to wait that long.
            self._stop.wait(0.3)

    def _connect_like_nvda(self) -> None:
        verified = ssl.create_default_context()
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
                verified.wrap_socket(raw, server_hostname="localhost").close()
            raise AssertionError("relay cert unexpectedly passed verification")
        except ssl.SSLCertVerificationError:
            pass
        pinned = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        pinned.check_hostname = False
        pinned.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as raw:
            pinned.wrap_socket(raw).close()  # the certificate fetch
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock = pinned.wrap_socket(raw)
        der = sock.getpeercert(binary_form=True) or b""
        if hashlib.sha256(der).hexdigest() != self.fingerprint:
            sock.close()
            raise AssertionError("relay fingerprint changed")
        sock.settimeout(None)
        self._sock = sock
        self.send({"type": "protocol_version", "version": 2})
        self.send({"type": "join", "channel": self.channel, "connection_type": "slave"})

    def _read_loop(self) -> None:
        buffer = b""
        while self._sock is not None:
            data = self._sock.recv(65536)
            if not data:
                return
            buffer += data
            # Once dropped, lines still buffered never reach NVDA.
            while b"\n" in buffer and self._sock is not None:
                line, buffer = buffer.split(b"\n", 1)
                self._handle(json.loads(line))

    # ------------------------------------------------------------ messages

    def send(self, message: dict[str, Any]) -> None:
        self.send_raw(json.dumps(message).encode() + b"\n")

    def send_raw(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise AssertionError("fake NVDA is not connected")
        with self._lock:
            sock.sendall(data)

    def say(self, *strings: str) -> None:
        self.send(CANCEL)
        self.send(speak(*strings))

    def _handle(self, message: dict[str, Any]) -> None:
        self.received.append(message)
        kind = message.get("type")
        if kind == "channel_joined":
            assert message["clients"] == [{"id": 1, "connection_type": "master"}]
            self.joins += 1
            self._joined.set()
        elif kind == "key":
            self._key(int(message["vk_code"]), pressed=bool(message["pressed"]))

    def _key(self, vk: int, *, pressed: bool) -> None:
        if pressed:
            self.held.add(vk)
            self._down.append(vk)
            if self.replies.get(self._name()) == DISCONNECT_MID_GESTURE and vk not in MODIFIER_VKS:
                self.replies.pop(self._name())
                self._drop()
            return
        self.held.discard(vk)
        if not self.held and self._down:
            name = self._name()
            self._down.clear()
            self.gestures.append(name)
            self._reply(name)

    def _name(self) -> str:
        return "+".join(VK_NAMES.get(vk, hex(vk)) for vk in self._down)

    def _reply(self, gesture: str) -> None:
        reply = self.replies.get(gesture, [CANCEL, speak(f"pressed {gesture}")])
        match reply:
            case "malformed":
                self.send_raw(b"{not json\n")
            case "oversize":
                self.send_raw(b'{"type":"speak","sequence":["' + b"x" * (1024 * 1024) + b'"]}\n')
            case "unknown":
                self.send({"type": "display", "cells": [1, 2]})
                self.say("after unknown")
            case "server_error":
                self.send({"type": "error", "message": "boom"})
            case "silence":
                pass
            case list():
                for message in reply:
                    self.send(message)
                    time.sleep(0.01)
            case _:
                raise AssertionError(reply)
