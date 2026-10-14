"""Error paths at the edges: relay joins, a busy keyboard, and bad daemon replies."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from collections.abc import Iterator

import pytest
from tests import fake_nvda as f
from tests.integration.conftest import Env, free_port, run_asc, start_asc, tls_client

from agent_speech_check import firewall, ipc, session, state, winapi
from agent_speech_check.errors import DaemonNoReply
from agent_speech_check.targets import SANDBOX_PORT, Target


def test_wrong_channel_key_gets_error_and_close(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    with tls_client(env.port) as sock:
        sock.sendall(b'{"type":"protocol_version","version":2}\n')
        sock.sendall(b'{"type":"join","channel":"wrong","connection_type":"slave"}\n')
        reply = sock.recv(4096)
        assert json.loads(reply) == {"type": "error", "message": "incorrect_password"}
        assert sock.recv(4096) == b""
    status = run_asc("status", "--target", "w").stdout
    assert "nvda: not connected" in status
    assert "last error: refused a join with the wrong channel key or role" in status


def test_second_press_waits_then_gives_up(env: Env) -> None:
    assert run_asc("start", "--target", "d").returncode == 0
    fake = env.fake("d", {"Space": f.SILENCE})
    try:
        slow = start_asc("press", "Space", "--settle", "3000", "--target", "d")
        # Once NVDA has the key, the slow press holds the keyboard for its settle.
        deadline = time.monotonic() + 10
        while not fake.gestures:
            assert time.monotonic() < deadline, "the slow press never reached NVDA"
            time.sleep(0.05)
        busy = run_asc("press", "Tab", "--timeout", "300", "--target", "d")
        assert busy.returncode == 4
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


@pytest.mark.parametrize("daemon_reply", [b"not json\n"], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_config_set_looks_for_the_daemon_before_saving(env: Env) -> None:
    path = env.root / "agent-speech-check" / "config.toml"
    before = path.read_text(encoding="utf-8")
    result = run_asc("config", "set", "port", "7000", "--target", "w")
    assert result.returncode == 6
    assert path.read_text(encoding="utf-8") == before


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


def test_daemon_that_does_not_answer_counts_as_running(env: Env) -> None:
    """A live daemon that can't be talked to (here: a token it doesn't know, which it
    drops without a reply) is still running: nothing may forget it, and stop ends it."""
    assert run_asc("start", "--target", "w").returncode == 0
    path = env.folder("w") / "state.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**current, "token": "not-its-token"}), encoding="utf-8")

    removed = run_asc("config", "remove-target", "w")
    assert removed.returncode == 4, removed.stderr
    started = run_asc("start", "--target", "w")
    # Refused, not "not connected": retrying start can't help, stopping can.
    assert started.returncode == 4, started.stderr
    assert 'asc is running for target "w" but doesn\'t answer' in started.stderr
    assert "hint: asc stop --target w, then asc start" in started.stderr
    assert path.exists()

    stopped = run_asc("stop", "--target", "w")
    assert stopped.returncode == 0, stopped.stderr
    assert not path.exists()
    assert winapi.image_path(current["pid"]) is None


def test_stop_leaves_a_process_newer_than_the_state_file(env: Env) -> None:
    """A pid in a dead daemon's file may since belong to another program."""
    assert run_asc("start", "--target", "w").returncode == 0
    path = env.folder("w") / "state.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**current, "token": "x", "started": 1.0}), encoding="utf-8")
    try:
        assert run_asc("stop", "--target", "w").returncode == 0
        assert not path.exists()
        assert winapi.image_path(current["pid"]) is not None
    finally:
        winapi.terminate(current["pid"], 10)


def test_stop_clears_a_state_file_without_a_pid(env: Env) -> None:
    """A state file this asc can't read names no process to end: stop drops it."""
    folder = env.folder("w")
    folder.mkdir(parents=True)
    (folder / "state.json").write_text('{"pid": "old"}', encoding="utf-8")
    assert run_asc("stop", "--target", "w").returncode == 0
    assert not (folder / "state.json").exists()
    assert run_asc("start", "--target", "w").returncode == 0


@pytest.mark.parametrize("daemon_reply", [b'{"v":1,"ok":true,"result":{}}\n'], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_stop_ends_a_daemon_that_agrees_but_stays(env: Env) -> None:
    """In-process with a short wait: the real one is 30 s."""
    session.stop(env.folder("w"), "w", timeout_s=0.5)
    assert not (env.folder("w") / "state.json").exists()


@pytest.mark.parametrize("daemon_reply", [b""], indirect=True)
@pytest.mark.usefixtures("daemon_reply")
def test_stop_says_when_it_cannot_end_a_daemon(env: Env) -> None:
    """Pid 4 is Windows' System process: running since boot, and no one may end it."""
    path = env.folder("w") / "state.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**current, "pid": 4, "started": time.time()}), encoding="utf-8")
    result = run_asc("stop", "--target", "w")
    assert result.returncode == 4, result.stderr
    assert 'cannot end the asc daemon for target "w" (pid 4)' in result.stderr
    assert path.exists()


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
    if result.returncode == 7:
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
def test_daemon_that_never_answers(env: Env) -> None:
    """In-process with a short wait: the real one is 5 s."""
    request = ipc.Request("status", ipc.NoArgs())
    with pytest.raises(DaemonNoReply, match="cannot talk to the asc daemon: no reply in time"):
        ipc.call(env.folder("w"), "w", request, 0.3)


@pytest.mark.usefixtures("env")
def test_too_many_gestures_send_nothing() -> None:
    result = run_asc("press", *(["Tab"] * 101), "--target", "w")
    assert result.returncode == 2
    assert "gestures: expected 1 to 100 key names" in result.stderr


def test_stop_does_not_wait_on_a_peer_that_went_silent(env: Env) -> None:
    """A sandbox's NVDA vanishes without closing when its sandbox stops. The
    daemon must still exit at once, not when the TLS close times out."""
    assert run_asc("start", "--target", "w").returncode == 0
    pid = state.read(env.folder("w"), "w").pid
    channel = env.channel("w")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection(("127.0.0.1", env.port), timeout=5) as raw,
        context.wrap_socket(raw) as sock,
    ):
        sock.sendall(json.dumps({"type": "protocol_version", "version": 2}).encode() + b"\n")
        join = {"type": "join", "channel": channel, "connection_type": "slave"}
        sock.sendall(json.dumps(join).encode() + b"\n")
        received = b""
        while b"channel_joined" not in received:
            received += sock.recv(65536)
        # From here on it never reads or answers, like a peer that is gone.
        stopped = time.monotonic()
        assert run_asc("stop", "--target", "w").returncode == 0
        while winapi.image_path(pid) is not None:
            assert time.monotonic() - stopped < 10, "the daemon waited on the silent peer"
            time.sleep(0.1)
