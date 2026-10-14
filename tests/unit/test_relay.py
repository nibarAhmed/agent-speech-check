from __future__ import annotations

import asyncio
import ssl
import time
from pathlib import Path
from types import TracebackType

import pytest

from agent_speech_check import relay
from agent_speech_check.errors import ProtocolError, ServerSentError
from agent_speech_check.speech_log import SpeechLog


@pytest.mark.asyncio
async def test_connection_that_never_joins_is_dropped(tmp_path: Path) -> None:
    r = relay.Relay("w", relay.load_or_create(tmp_path), SpeechLog(), join_timeout_s=0.5)
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
    r = relay.Relay("d", relay.load_or_create(tmp_path), SpeechLog())
    r._lost(ServerSentError("boom"))
    depths = []
    for _ in range(3):
        with pytest.raises(ProtocolError, match="NVDA reported an error") as caught:
            r.check(0, drop_fails=False)
        depths.append(_depth(caught.value.__traceback__))
    assert depths[0] == depths[1] == depths[2]
