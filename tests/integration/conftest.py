"""Shared setup: every test gets its own state folder (LOCALAPPDATA) and relay port."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.fake_nvda import FakeNvda, Reply

from agent_speech_check.relay import fingerprint


def run_asc(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "agent_speech_check", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass
class Env:
    root: Path
    port: int

    def folder(self, target: str) -> Path:
        return self.root / "agent-speech-check" / target

    def fake(self, target: str, replies: dict[str, Reply | str] | None = None) -> FakeNvda:
        """A fake NVDA connected to the target's relay, using its stored key and cert."""
        folder = self.folder(target)
        channel = json.loads((folder / "relay.json").read_text(encoding="utf-8"))["channel"]
        fake = FakeNvda(self.port, channel, fingerprint(folder / "relay_cert.pem"), replies or {})
        return fake.start()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    port = free_port()
    config = tmp_path / "agent-speech-check"
    config.mkdir()
    (config / "targets.toml").write_text(
        f'[targets.w]\nmode = "watch"\nport = {port}\n'
        f'[targets.d]\nmode = "desktop"\nport = {port}\nlaunch = false\n',
        encoding="utf-8",
    )
    yield Env(tmp_path, port)
    # Never leave a daemon behind, even when a test failed half way.
    for state in config.glob("*/state.json"):
        if run_asc("stop", "--target", state.parent.name).returncode != 0:
            pid = json.loads(state.read_text(encoding="utf-8"))["pid"]
            subprocess.run(["taskkill", "/f", "/pid", str(pid)], check=False)  # noqa: S603, S607
    assert os.environ["LOCALAPPDATA"] == str(tmp_path)
