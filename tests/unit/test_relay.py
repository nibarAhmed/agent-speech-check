from __future__ import annotations

import asyncio
import json
import ssl
import time
from pathlib import Path
from types import TracebackType

import pytest

from agent_speech_check.errors import (
    ProtocolError,
    RelayFileBroken,
    ServerSentError,
    SetupNeeded,
)
from agent_speech_check.remote import relay
from agent_speech_check.remote.speech_log import SpeechLog


@pytest.mark.asyncio
async def test_connection_that_never_joins_is_dropped(tmp_path: Path) -> None:
    r = relay.Relay(relay.load_or_create(tmp_path), SpeechLog(), join_timeout_s=0.5)
    server = await r.start(0)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    async with server:
        reader, writer = await asyncio.open_connection(r.host, r.port, ssl=context)
        started = time.monotonic()
        assert await asyncio.wait_for(reader.read(), 5) == b""
        assert 0.4 < time.monotonic() - started < 3
        writer.close()
        await writer.wait_closed()
    assert r.last_error == "the connection sent no join within 0.5 s"
    assert not r.connected
    assert r.losses == []


def _depth(tb: TracebackType | None) -> int:
    depth = 0
    while tb is not None:
        depth += 1
        tb = tb.tb_next
    return depth


def test_a_kept_protocol_error_does_not_grow_each_time_it_is_raised(tmp_path: Path) -> None:
    r = relay.Relay(relay.load_or_create(tmp_path), SpeechLog())
    r._lost(ServerSentError("boom"))
    depths = []
    for _ in range(3):
        with pytest.raises(ProtocolError, match="NVDA reported an error") as caught:
            r.check(0, drop_fails=False)
        depths.append(_depth(caught.value.__traceback__))
    assert depths[0] == depths[1] == depths[2]


@pytest.mark.parametrize("content", ["not json", '{"channel": ""}', "[]"])
def test_a_broken_relay_file_needs_deleting_not_new_arguments(tmp_path: Path, content: str) -> None:
    # asc wrote relay.json itself, so no argument fixes it; deleting it does.
    relay.load_or_create(tmp_path)
    (tmp_path / "relay.json").write_text(content, encoding="utf-8")
    with pytest.raises(SetupNeeded) as exc:
        relay.load_or_create(tmp_path)
    assert str(tmp_path / "relay.json") in exc.value.message
    assert exc.value.hint is not None
    assert "delete" in exc.value.hint


def test_a_relay_file_that_cannot_be_read_needs_setup(tmp_path: Path) -> None:
    (tmp_path / "relay.json").mkdir()
    with pytest.raises(SetupNeeded) as exc:
        relay.load_or_create(tmp_path)
    assert str(tmp_path / "relay.json") in exc.value.message
    assert exc.value.hint is not None
    assert "permissions" in exc.value.hint


def test_a_relay_folder_that_cannot_be_made_needs_setup(tmp_path: Path) -> None:
    folder = tmp_path / "target"
    folder.write_text("", encoding="utf-8")
    with pytest.raises(SetupNeeded) as exc:
        relay.load_or_create(folder)
    assert str(folder) in exc.value.message


def test_a_relay_file_that_is_not_utf8_needs_deleting(tmp_path: Path) -> None:
    (tmp_path / "relay.json").write_bytes(b'{"channel": "\xff"}')
    with pytest.raises(RelayFileBroken):
        relay.load_or_create(tmp_path)


def test_a_broken_relay_cert_needs_deleting(tmp_path: Path) -> None:
    relay.load_or_create(tmp_path)
    (tmp_path / "relay_cert.pem").write_text("not a cert", encoding="utf-8")
    with pytest.raises(RelayFileBroken) as exc:
        relay.load_or_create(tmp_path)
    assert str(tmp_path / "relay_cert.pem") in exc.value.message


@pytest.mark.asyncio
async def test_a_flood_of_blank_lines_before_the_join_is_skipped(tmp_path: Path) -> None:
    credentials = relay.load_or_create(tmp_path)
    r = relay.Relay(credentials, SpeechLog())
    server = await r.start(0)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    join = {"type": "join", "channel": credentials.channel, "connection_type": "slave"}
    async with server:
        reader, writer = await asyncio.open_connection(r.host, r.port, ssl=context)
        writer.write(b"\n" * 5000 + json.dumps(join).encode() + b"\n")
        reply = json.loads(await asyncio.wait_for(reader.readline(), 5))
        writer.close()
        await writer.wait_closed()
    assert reply["type"] == "channel_joined"
