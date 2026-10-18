"""daemon.log never holds an exception's message, which can carry speech or a request's text."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from agent_speech_check.daemon import ipc, server
from agent_speech_check.errors import IPC_VERSION

# Built away from the raise lines, which a traceback quotes.
PRIVATE = " ".join(("my", "password"))


@pytest.fixture
def daemon_log() -> Iterator[io.StringIO]:
    """What the daemon's log file would get, through its own formatter."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(server.LogFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield out
    finally:
        root.removeHandler(handler)


def _lookup() -> None:
    raise KeyError(PRIVATE)


def _fail() -> None:
    try:
        _lookup()
    except KeyError as error:
        raise ValueError(PRIVATE) from error


def _fail_in_a_group() -> None:
    try:
        _fail()
    except ValueError as error:
        raise ExceptionGroup(PRIVATE, [error, OSError(PRIVATE)]) from None


def test_a_traceback_keeps_its_frames_and_types_but_no_messages(daemon_log: io.StringIO) -> None:
    try:
        _fail_in_a_group()
    except ExceptionGroup:
        logging.getLogger("test").exception("it broke")
    text = daemon_log.getvalue()
    assert "it broke" in text
    for kind in ("ExceptionGroup", "ValueError", "KeyError", "OSError"):
        assert f"{kind} (message not logged)" in text
    assert "in _lookup" in text
    assert "caused by:" in text
    assert PRIVATE not in text


@pytest.mark.asyncio
async def test_a_bug_while_handling_a_request_logs_no_request_text(
    daemon_log: io.StringIO,
) -> None:
    async def handler(request: ipc.Request) -> dict[str, Any]:
        raise RuntimeError(f"bug with {request}")

    server_ = await asyncio.start_server(
        lambda r, w: ipc.serve_one("token", handler, r, w), "127.0.0.1", 0
    )
    port = server_.sockets[0].getsockname()[1]
    request = {
        "v": IPC_VERSION,
        "token": "token",
        "cmd": "type",
        "args": {"text": PRIVATE, "settle_ms": 400, "timeout_ms": 5000},
    }
    async with server_:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(json.dumps(request).encode() + b"\n")
        reply = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
    # The caller hears the bug in full; the log keeps only its type and where it was.
    assert PRIVATE in reply["error"]["message"]
    text = daemon_log.getvalue()
    assert "internal error handling 'type'" in text
    assert "RuntimeError (message not logged)" in text
    assert PRIVATE not in text
