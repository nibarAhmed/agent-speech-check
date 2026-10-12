"""What a running wait or launch does when a connection fails or NVDA goes away.

Only NVDA's own joined connection can fail a command. A connection that never
joins can't be told apart from a stranger's, so its failure shows only in
`asc status`.
"""

from __future__ import annotations

import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest
from tests.fake_nvda import FakeNvda
from tests.integration.conftest import Env, run_asc


@pytest.fixture
def nvda(env: Env) -> Iterator[FakeNvda]:
    assert run_asc("start", "--target", "d").returncode == 0
    fake = env.fake("d")
    yield fake
    fake.close()


def _asc_in_background(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "agent_speech_check", *args, "--target", "d"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )


def _stranger(port: int) -> ssl.SSLSocket:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=15))


def test_a_strangers_bad_connection_does_not_fail_a_wait(env: Env, nvda: FakeNvda) -> None:
    waiting = _asc_in_background("wait", "later", "--timeout", "8000")
    time.sleep(1)
    with _stranger(env.port) as sock:
        sock.sendall(b"{not json\n")
        assert sock.recv(4096) == b""
    nvda.say("later")
    out, err = waiting.communicate(timeout=30)
    assert waiting.returncode == 0, err
    assert 'said: "later"' in out
    status = run_asc("status", "--target", "d").stdout
    assert "last error: NVDA sent a line that is not a JSON object" in status


@pytest.mark.parametrize("command", [("wait", "never"), ("launch", f'"{sys.executable}" -c pass')])
def test_a_protocol_fault_from_nvda_fails_wait_and_launch(
    nvda: FakeNvda, command: tuple[str, str]
) -> None:
    running = _asc_in_background(*command, "--timeout", "8000")
    time.sleep(1)
    nvda.send_raw(b"{not json\n")
    _, err = running.communicate(timeout=30)
    assert running.returncode == 5, err
    assert "NVDA sent a line that is not a JSON object" in err


def test_nvda_that_goes_and_stays_away_fails_a_wait_with_3(nvda: FakeNvda) -> None:
    waiting = _asc_in_background("wait", "never", "--timeout", "3000")
    time.sleep(1)
    nvda.close()
    _, err = waiting.communicate(timeout=30)
    assert waiting.returncode == 3, err
    assert 'NVDA disconnected from target "d" and did not come back within 3000 ms' in err


def test_wrong_channel_key_shows_in_status(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    with _stranger(env.port) as sock:
        sock.sendall(b'{"type":"join","channel":"wrong","connection_type":"slave"}\n')
        sock.recv(4096)
    status = run_asc("status", "--target", "w").stdout
    assert "last error: refused a join with the wrong channel key or role" in status
