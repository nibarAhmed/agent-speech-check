"""Error paths at the edges: relay joins, a busy keyboard, and bad daemon replies."""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

import pytest
from tests import fake_nvda as f
from tests.integration.conftest import Env, run_asc


def _tls(port: int) -> ssl.SSLSocket:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=15))


def test_wrong_channel_key_gets_error_and_close(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    with _tls(env.port) as sock:
        sock.sendall(b'{"type":"protocol_version","version":2}\n')
        sock.sendall(b'{"type":"join","channel":"wrong","connection_type":"slave"}\n')
        reply = sock.recv(4096)
        assert json.loads(reply) == {"type": "error", "message": "incorrect_password"}
        assert sock.recv(4096) == b""
    assert "nvda: not connected" in run_asc("status", "--target", "w").stdout


def test_connection_that_never_joins_is_dropped(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    with _tls(env.port) as sock:
        started = time.monotonic()
        assert sock.recv(4096) == b""
        assert 9 < time.monotonic() - started < 14
    status = run_asc("status", "--target", "w").stdout
    assert "last error: the connection sent no join within 10 s" in status


def test_second_press_waits_then_gives_up(env: Env) -> None:
    assert run_asc("start", "--target", "d").returncode == 0
    fake = env.fake("d", {"Space": f.SILENCE})
    try:
        slow = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_speech_check",
                "press",
                "Space",
                "--settle",
                "3000",
                "--target",
                "d",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)
        busy = run_asc("press", "Tab", "--timeout", "300", "--target", "d")
        assert busy.returncode == 1
        assert "another asc command kept the keyboard" in busy.stderr
        assert slow.wait(30) == 0
        assert fake.gestures == ["Space"]
        assert fake.held == set()
    finally:
        fake.close()


class FakeDaemon:
    """A listener that answers one IPC request with a fixed reply."""

    def __init__(self, reply: bytes) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.server.getsockname()[1]
        self.reply = reply
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self.server.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(self.reply)


@pytest.fixture
def daemon_reply(env: Env, request: pytest.FixtureRequest) -> Iterator[None]:
    fake = FakeDaemon(request.param)
    folder = env.folder("w")
    folder.mkdir(parents=True)
    state = {"pid": 1, "port": fake.port, "token": "t", "started": 0}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    yield
    (folder / "state.json").unlink(missing_ok=True)
    fake.server.close()


@pytest.mark.parametrize("daemon_reply", [b"not json\n"], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_garbage_from_daemon_is_internal_error() -> None:
    result = run_asc("status", "--target", "w")
    assert result.returncode == 6
    assert "daemon response: expected JSON" in result.stderr


@pytest.mark.parametrize("daemon_reply", [b'{"v":99,"ok":true,"result":{}}\n'], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_old_daemon_version() -> None:
    result = run_asc("status", "--target", "w")
    assert result.returncode == 3
    assert "speaks IPC version 99, not 1" in result.stderr
    assert "hint: asc stop, then asc start" in result.stderr


@pytest.mark.parametrize("daemon_reply", [b""], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_daemon_hangs_up() -> None:
    result = run_asc("status", "--target", "w")
    assert result.returncode == 3
    assert "closed the connection without a reply" in result.stderr
