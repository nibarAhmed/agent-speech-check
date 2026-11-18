"""Remote mode through a real ssh tunnel. They skip unless ASC_E2E_SSH names an ssh
destination whose 127.0.0.1 is this machine, as e2e.yml's Orca job does with root@localhost.
A fake NVDA stands in for the one on the other machine.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import psutil
import pytest
from tests import fake_nvda as f
from tests.integration.conftest import Env, free_port, run_asc, wait_until

from agent_speech_check.daemon import session, state

DESTINATION = os.environ.get("ASC_E2E_SSH", "")
INSTANCE = "e2e-ssh"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not DESTINATION, reason="needs ASC_E2E_SSH"),
]


def tunnels(env: Env) -> list[int]:
    """The ssh processes this test's daemon runs."""
    folder = env.folder("remote", INSTANCE)
    pid = json.loads((folder / state.FILE).read_text(encoding="utf-8"))["pid"]
    return [p.pid for p in psutil.Process(pid).children() if p.name() == "ssh"]


def is_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def test_nvda_joins_through_the_tunnel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for home in ("LOCALAPPDATA", "XDG_STATE_HOME"):
        monkeypatch.setenv(home, str(tmp_path))
    port = free_port()
    env = Env(tmp_path, port)
    remote = f"[remote]\nos = 'windows'\nssh = '{DESTINATION}'\nport = {port}\n"
    env.config(remote, instance=INSTANCE)
    started = run_asc("start", "--mode", "remote", "--instance", INSTANCE)
    assert started.returncode == 0, started.stderr
    assert f"over ssh: {DESTINATION}, tunnel up" in started.stdout
    assert f"host 127.0.0.1:{port}" in started.stdout
    try:
        fake = env.fake("remote", {"Tab": [f.speak("first")]}, instance=INSTANCE, port=port)
        try:
            pressed = run_asc("press", "Tab", "--instance", INSTANCE)
            assert pressed.stdout == '1 said: "first"\n', pressed.stderr
        finally:
            fake.close()
        # A lost tunnel is made again. The ssh ended is this daemon's own child.
        [lost] = tunnels(env)
        psutil.Process(lost).kill()

        def up_again() -> bool:
            current = session.status(env.folder("remote", INSTANCE))["tunnel"]["up"]
            return current and tunnels(env) not in ([], [lost])

        assert wait_until(up_again, timeout_s=60)
        fake = env.fake("remote", {"Tab": [f.speak("second")]}, instance=INSTANCE, port=port)
        try:
            pressed = run_asc("press", "Tab", "--instance", INSTANCE)
            assert pressed.stdout.endswith('said: "second"\n'), pressed.stderr
        finally:
            fake.close()
    finally:
        stopped = run_asc("stop", "--instance", INSTANCE)
    assert stopped.returncode == 0, stopped.stderr
    # Stopping ends ssh, so the other machine's port is free again.
    assert wait_until(lambda: is_free(port), timeout_s=20)


def test_a_taken_port_on_the_other_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for home in ("LOCALAPPDATA", "XDG_STATE_HOME"):
        monkeypatch.setenv(home, str(tmp_path))
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        env = Env(tmp_path, port)
        remote = f"[remote]\nos = 'windows'\nssh = '{DESTINATION}'\nport = {port}\n"
        env.config(remote, instance=INSTANCE)
        started = run_asc("start", "--mode", "remote", "--instance", INSTANCE)
    assert started.returncode == 4, started.stdout + started.stderr
    assert f"port {port}" in started.stderr
    assert f"asc config set port PORT --mode remote --instance {INSTANCE}" in started.stderr
    assert run_asc("status", "--instance", INSTANCE).returncode == 3
