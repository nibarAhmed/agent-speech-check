"""`asc config` and `asc setup` next to a running daemon, and settings used by commands."""

from __future__ import annotations

import pytest
from tests.integration.conftest import Env, run_asc

from agent_speech_check.cli import run_setup
from agent_speech_check.errors import AlreadyRunning


def test_changes_while_running(env: Env) -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    changed = run_asc("config", "set", "port", str(env.port), "--mode", "watch")
    assert changed.returncode == 0, changed.stderr
    assert changed.stdout.endswith("applies at the next asc start\n")
    # Another mode's settings don't wait.
    other = run_asc("config", "set", "share", str(env.root), "--mode", "sandbox")
    assert other.stdout == f"share = {env.root} (sandbox mode)\n"
    # Command defaults are read by every command, so they apply at once.
    timing = run_asc("config", "set", "timeout_ms", "300")
    assert timing.stdout == "timeout_ms = 300 (global)\n"
    # Global, but a daemon takes the panic key at its start.
    panic = run_asc("config", "set", "panic_key", "ctrl+shift+alt+f20")
    assert panic.returncode == 0, panic.stderr
    assert panic.stdout.endswith("applies at the next asc start\n")

    with pytest.raises(AlreadyRunning, match="asc is already running"):
        run_setup(lambda _: "", replace_without_asking=True)


def test_timeout_setting_used_by_wait(env: Env) -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    fake = env.fake("watch")
    try:
        assert run_asc("config", "set", "timeout_ms", "300").returncode == 0
        waited = run_asc("wait", "never")
        assert waited.returncode == 1
        assert "within 300 ms" in waited.stderr
        flagged = run_asc("wait", "never", "--timeout", "200")
        assert "within 200 ms" in flagged.stderr
    finally:
        fake.close()
