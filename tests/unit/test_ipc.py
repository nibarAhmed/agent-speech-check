from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from agent_speech_check.daemon import ipc
from agent_speech_check.errors import IPC_VERSION


@pytest.mark.asyncio
async def test_a_bug_while_handling_a_request_logs_no_request_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: ipc.Request) -> dict[str, Any]:
        raise RuntimeError(f"bug with {request}")

    server = await asyncio.start_server(
        lambda r, w: ipc.serve_one("token", handler, r, w), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    request = {
        "v": IPC_VERSION,
        "token": "token",
        "cmd": "type",
        "args": {"text": "my password", "settle_ms": 400, "timeout_ms": 5000},
    }
    with caplog.at_level(logging.INFO, logger=ipc.__name__):
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(json.dumps(request).encode() + b"\n")
            reply = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
    # The caller hears the bug in full; the log keeps only its type and where it was.
    assert "my password" in reply["error"]["message"]
    assert "RuntimeError" in caplog.text
    assert "test_ipc.py" in caplog.text
    assert "my password" not in caplog.text
