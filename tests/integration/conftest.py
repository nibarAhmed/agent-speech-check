"""Shared setup: every test gets its own state folder (LOCALAPPDATA) and relay port."""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import socket
import ssl
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import psutil
import pytest
from tests.fake_nvda import FakeNvda, Reply

from agent_speech_check.relay import fingerprint
from agent_speech_check.wait import poll_until


def run_asc(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "agent_speech_check", *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=timeout,
    )


def start_asc(*args: str) -> subprocess.Popen[str]:
    """Run asc in the background, for a command that must still be running."""
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "agent_speech_check", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )


def tls_client(port: int) -> ssl.SSLSocket:
    """A TLS connection to the relay that doesn't check its cert, like a stranger's."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=15))


def wait_until(condition: Callable[[], bool], timeout_s: float = 10) -> bool:
    """Poll until condition holds or timeout_s passes, and say which happened."""
    return poll_until(condition, timeout_s, 0.05)


# Port 0 hands out a port from Windows' dynamic range (49152 up), where every outgoing
# connection also takes its source port: another worker could grab it before the daemon
# binds it. So ports come from below that range, one block per xdist worker.
PORT_BASE = 20000
PORTS_PER_WORKER = 500
_next_port = itertools.count()


def free_port() -> int:
    worker = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw"))
    block = PORT_BASE + worker * PORTS_PER_WORKER
    for _ in range(PORTS_PER_WORKER):
        port = block + next(_next_port) % PORTS_PER_WORKER
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    pytest.fail(f"no free port in {block}-{block + PORTS_PER_WORKER - 1}")


@dataclass
class Env:
    root: Path
    port: int

    def folder(self, target: str) -> Path:
        return self.root / "agent-speech-check" / target

    def channel(self, target: str) -> str:
        relay = (self.folder(target) / "relay.json").read_text(encoding="utf-8")
        channel: str = json.loads(relay)["channel"]
        return channel

    def fake(self, target: str, replies: dict[str, Reply | str] | None = None) -> FakeNvda:
        """A fake NVDA connected to the target's relay, using its stored key and cert."""
        folder = self.folder(target)
        cert = fingerprint(folder / "relay_cert.pem")
        fake = FakeNvda(self.port, self.channel(target), cert, replies or {})
        return fake.start()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    port = free_port()
    config = tmp_path / "agent-speech-check"
    config.mkdir()
    (config / "config.toml").write_text(
        f'[targets.w]\nmode = "watch"\nport = {port}\n'
        f'[targets.d]\nmode = "desktop"\nport = {port}\nlaunch = false\n',
        encoding="utf-8",
    )
    yield Env(tmp_path, port)
    # Never leave a daemon behind, even when a test failed half way.
    for state in config.glob("*/state.json"):
        if run_asc("stop", "--target", state.parent.name).returncode != 0:
            _kill_if_ours(json.loads(state.read_text(encoding="utf-8"))["pid"])
    assert os.environ["LOCALAPPDATA"] == str(tmp_path)


def _kill_if_ours(pid: int) -> None:
    """Kill a stuck test daemon, but only if the pid is still a Python process.

    A dead daemon's pid can be reused, maybe by the user's own NVDA.
    """
    with contextlib.suppress(psutil.NoSuchProcess):
        process = psutil.Process(pid)
        if process.name().lower().startswith("python"):
            process.kill()
