"""The relay NVDA connects out to (see `docs/protocol.md`).

`asc` is both the relay and the leader. NVDA, the follower, connects to
127.0.0.1 and joins the channel with the mode's key. Before it joins it makes
two throwaway connections (a verified TLS attempt that fails, then a certificate
fetch), so the relay accepts and forgets connections that fail or close early.

The certificate and channel key are kept per mode, because NVDA pins the
certificate's fingerprint and a user in watch mode types the key once.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import ipaddress
import json
import logging
import secrets
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_speech_check.errors import (
    AscError,
    FileUnusable,
    JoinRefused,
    LineTooLong,
    NoJoin,
    NvdaNotConnected,
    ProtocolError,
    RelayCertBroken,
    RelayFileBroken,
    RelayKeyBroken,
)
from agent_speech_check.inputs.keys import Key
from agent_speech_check.remote import protocol
from agent_speech_check.remote.speech_log import SpeechLog
from agent_speech_check.validate import Fields
from agent_speech_check.windows.paths import write_atomic

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
JOIN_TIMEOUT_S = 10.0
# A peer that is gone (killed, or its sandbox stopped) never answers the TLS close.
CLOSE_TIMEOUT_S = 1.0
KEEPALIVE_S = 120.0
CERT_DAYS = 3650


@dataclass(frozen=True)
class Credentials:
    cert: Path
    key: Path
    fingerprint: str
    channel: str


def _new_cert(cert_path: Path, key_path: Path) -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "asc relay")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address(HOST))]
            ),
            critical=False,
        )
        .sign(private, hashes.SHA256())
    )
    write_atomic(
        key_path,
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    write_atomic(cert_path, cert.public_bytes(serialization.Encoding.PEM))


def fingerprint(cert_path: Path) -> str:
    """Lowercase hex SHA-256 of the DER cert, the form NVDA's trustedCertificates uses."""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except ValueError:
        raise RelayCertBroken(str(cert_path)) from None
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def _check_key(cert_path: Path, key_path: Path) -> None:
    """The key loads as the cert's own, as the relay's TLS will load them."""
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert_path, key_path)
    except ssl.SSLError:
        raise RelayKeyBroken(str(key_path)) from None


def load_or_create(folder: Path) -> Credentials:
    """The mode's relay cert and channel key, created on first use and then kept."""
    try:
        return _load_or_create(folder)
    except OSError as error:
        # The folder and the reads; write_atomic raises FileUnusable itself.
        raise FileUnusable.from_os(error.filename or folder, error) from None


def _load_or_create(folder: Path) -> Credentials:
    cert, key, info = folder / "relay_cert.pem", folder / "relay_key.pem", folder / "relay.json"
    folder.mkdir(parents=True, exist_ok=True)
    if not (cert.exists() and key.exists()):
        _new_cert(cert, key)
    channel: str | None = None
    if info.exists():
        try:
            raw = json.loads(info.read_text(encoding="utf-8"))
        except ValueError:
            raw = None
        channel = Fields(raw, str(info), RelayFileBroken).text("channel", max_len=200)
    if channel is None:
        channel = secrets.token_urlsafe(12)
        write_atomic(info, json.dumps({"channel": channel}))
    credentials = Credentials(cert, key, fingerprint(cert), channel)
    _check_key(cert, key)
    return credentials


class Relay:
    """Holds at most one follower connection. The newest NVDA to join wins.

    Transport only. `on_join` runs each time NVDA joins, before anything else
    can be sent, so the keyboard can release keys that a lost connection may
    have left down. It must not await, or a press could get in first.
    """

    def __init__(
        self,
        credentials: Credentials,
        speech: SpeechLog,
        *,
        join_timeout_s: float = JOIN_TIMEOUT_S,
    ) -> None:
        self.join_timeout_s = join_timeout_s
        self.credentials = credentials
        self.speech = speech
        self.on_join: Callable[[], None] = lambda: None
        self._ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ssl.load_cert_chain(credentials.cert, credentials.key)
        self._writer: asyncio.StreamWriter | None = None
        # Any failed connection, for `asc status`. Cleared when NVDA joins.
        self.last_error: str | None = None
        # One entry per lost NVDA connection: the message of the protocol error
        # that ended it, or None. A command notes len(losses) when it starts and
        # passes it to `check`. A connection that never joined can't be told
        # apart from a stranger's, so it never lands here.
        self.losses: list[str | None] = []
        self.host = HOST
        self.port = 0

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def start(self, port: int, host: str = HOST) -> asyncio.Server:
        """Listen on exactly one address: 127.0.0.1, or the Default Switch address
        for sandbox mode. Never on all interfaces."""
        server = await asyncio.start_server(
            self._handle,
            host,
            port,
            ssl=self._ssl,
            ssl_handshake_timeout=self.join_timeout_s,
            limit=protocol.MAX_LINE + 1,
        )
        self.host, self.port = host, server.sockets[0].getsockname()[1]
        return server

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        reason: ProtocolError | None = None
        try:
            joined = await asyncio.wait_for(self._join(reader, writer), self.join_timeout_s)
            if joined:
                await self._follow(reader, writer)
        except TimeoutError:
            self._failed(NoJoin(self.join_timeout_s))
        except ProtocolError as error:
            reason = error
            self._failed(error)
        except (ConnectionError, ssl.SSLError) as error:
            log.info("connection lost: %s", type(error).__name__)
        finally:
            if self._writer is writer:
                self._lost(reason)
            writer.close()
            try:
                async with asyncio.timeout(CLOSE_TIMEOUT_S):
                    await writer.wait_closed()
            except TimeoutError:
                writer.transport.abort()
            except OSError:  # ConnectionError or ssl.SSLError
                pass

    def _failed(self, error: AscError) -> None:
        self.last_error = error.message
        log.warning("dropped a connection: %s", error.message)

    def _lost(self, reason: AscError | None = None) -> None:
        self._writer = None
        self.losses.append(None if reason is None else reason.message)
        log.info("NVDA disconnected")
        self.speech.activity()

    def check(self, since: int, *, drop_fails: bool) -> None:
        """Raise if NVDA's connection broke after `since`, a length of `losses`.

        A protocol error that ended it is raised. With `drop_fails`, any lost
        connection, or none now, raises `NvdaNotConnected`, even if NVDA came back.
        """
        new = self.losses[since:]
        for reason in new:
            if reason is not None:
                raise ProtocolError(reason)
        if drop_fails and (new or not self.connected):
            raise NvdaNotConnected

    async def _read(self, reader: asyncio.StreamReader) -> protocol.Message | None:
        try:
            line = await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError as error:
            if error.partial.strip():
                return protocol.parse_message(error.partial)
            return None
        except asyncio.LimitOverrunError:
            raise LineTooLong(protocol.MAX_LINE) from None
        if not line.strip():
            return await self._read(reader)
        return protocol.parse_message(line.rstrip(b"\r\n"))

    async def _join(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """Wait for `join`. False when the connection closes first (a cert fetch)."""
        while True:
            message = await self._read(reader)
            if message is None:
                return False
            if isinstance(message, protocol.Join):
                break
        if message.channel != self.credentials.channel or message.connection_type != "slave":
            self._failed(JoinRefused())
            writer.write(protocol.encode_error("incorrect_password"))
            await writer.drain()
            return False
        writer.write(protocol.encode_channel_joined(message.channel))
        await writer.drain()
        old = self._writer
        if old is not None:
            self._lost()
            old.close()
        self._writer = writer
        self.last_error = None
        log.info("NVDA joined")
        self.on_join()
        return True

    async def _follow(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while self._writer is writer:
            message = await self._read(reader)
            if message is None:
                return
            self._dispatch(message)

    def _dispatch(self, message: protocol.Message) -> None:
        match message:
            case protocol.Speak(text=text):
                log.debug("speak, %d characters", len(text))
                self.speech.said(text)
            case protocol.Wave(name=name):
                log.debug("wave %s", name)
                self.speech.sound(name)
            case protocol.Unknown(type=kind):
                log.info("unknown message type %r", kind)
                self.speech.activity()
            case protocol.Ping():
                pass
            case _:
                self.speech.activity()

    def write_key(self, key: Key, *, pressed: bool) -> asyncio.StreamWriter:
        """Queue one key event without waiting. Only `keyboard.Keyboard` sends keys."""
        writer = self._writer
        if writer is None:
            raise NvdaNotConnected
        writer.write(protocol.encode_key(key.vk, extended=key.extended, pressed=pressed))
        return writer

    async def send_key(self, key: Key, *, pressed: bool) -> None:
        """Send one key event and wait until it is written."""
        writer = self.write_key(key, pressed=pressed)
        try:
            await writer.drain()
        except ConnectionError:
            self.disconnect()
            raise NvdaNotConnected from None

    async def keepalive(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            writer = self._writer
            if writer is not None:
                with contextlib.suppress(ConnectionError):
                    writer.write(protocol.encode_ping())
                    await writer.drain()

    def disconnect(self) -> None:
        writer = self._writer
        if writer is not None:
            self._lost()
            writer.close()
