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
from tests.integration.conftest import Env, free_port, run_asc

from agent_speech_check import firewall
from agent_speech_check.targets import SANDBOX_PORT, Target


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
    """A listener that answers one IPC request with a fixed reply, or HANG."""

    def __init__(self, reply: bytes) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.server.getsockname()[1]
        self.reply = reply
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self.server.accept()
        with conn:
            conn.recv(65536)
            if self.reply == HANG:
                time.sleep(30)
            else:
                conn.sendall(self.reply)


HANG = b"hang"


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


def _ipc_port(env: Env) -> int:
    state = json.loads((env.folder("w") / "state.json").read_text(encoding="utf-8"))
    port: int = state["port"]
    return port


@pytest.mark.parametrize(
    "request_line",
    [
        b'{"v":1,"token":"wrong","cmd":"status","args":{}}\n',
        b"not json at all\n",
        b"[1,2,3]\n",
    ],
)
def test_ipc_drops_bad_requests_without_a_reply(env: Env, request_line: bytes) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    with socket.create_connection(("127.0.0.1", _ipc_port(env)), timeout=10) as sock:
        sock.sendall(request_line)
        assert sock.recv(4096) == b""
    assert run_asc("status", "--target", "w").returncode == 0


def test_corrupt_state_file_means_not_running(env: Env) -> None:
    folder = env.folder("w")
    folder.mkdir(parents=True)
    (folder / "state.json").write_text("{half", encoding="utf-8")
    result = run_asc("status", "--target", "w")
    assert result.returncode == 3
    assert "not running" in result.stderr
    assert not (folder / "state.json").exists()


def test_corrupt_relay_file_is_a_clear_error(env: Env) -> None:
    folder = env.folder("w")
    folder.mkdir(parents=True)
    (folder / "relay.json").write_text("{half", encoding="utf-8")
    result = run_asc("start", "--target", "w")
    assert result.returncode == 3
    assert "relay.json: expected an object" in result.stderr


@pytest.mark.skipif(
    not firewall.rule_ok(SANDBOX_PORT, firewall.daemon_program()),
    reason="needs the asc sandbox-setup rule on this machine",
)
@pytest.mark.usefixtures("env")
def test_sandbox_setup_is_a_no_op_when_done() -> None:
    result = run_asc("sandbox-setup")
    assert result.returncode == 0
    assert result.stdout == f"firewall rule already in place for port {SANDBOX_PORT}\n"


def test_start_replaces_a_stale_state_file(env: Env) -> None:
    folder = env.folder("w")
    folder.mkdir(parents=True)
    stale = {"pid": 1, "port": free_port(), "token": "old", "started": 0}
    (folder / "state.json").write_text(json.dumps(stale), encoding="utf-8")
    started = run_asc("start", "--target", "w")
    assert started.returncode == 0, started.stderr
    fresh = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    assert fresh["token"] != stale["token"]


@pytest.mark.usefixtures("env")
def test_keys_finds_your_own_nvda() -> None:
    result = run_asc("keys", "report", "title")
    if result.returncode == 3:
        assert "NVDA not found. Looked in:" in result.stderr
    else:
        assert result.returncode == 0
        assert "Report title" in result.stdout


def test_stop_with_nvda_connected_closes_its_connection(env: Env) -> None:
    assert run_asc("start", "--target", "d").returncode == 0
    fake = env.fake("d", {"Tab": [f.speak("x")]})
    try:
        assert run_asc("press", "Tab", "--target", "d").returncode == 0
        assert run_asc("stop", "--target", "d").returncode == 0
        deadline = time.monotonic() + 10
        while fake.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not fake.connected
        assert fake.held == set()
    finally:
        fake.close()


def test_the_named_mutex_refuses_a_second_daemon(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    target = Target("w", "watch", env.port, keys=False, launch=False)
    second = run_asc("_daemon", target.to_json())
    assert second.returncode == 4
    assert 'asc is already running for target "w"' in second.stderr


@pytest.mark.parametrize("daemon_reply", [HANG], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_daemon_that_never_answers() -> None:
    result = run_asc("status", "--target", "w")
    assert result.returncode == 3
    assert "cannot talk to the asc daemon: no reply in time" in result.stderr


@pytest.mark.usefixtures("env")
def test_too_many_gestures_send_nothing() -> None:
    result = run_asc("press", *(["Tab"] * 101), "--target", "w")
    assert result.returncode == 2
    assert "gestures: expected 1 to 100 key names" in result.stderr
