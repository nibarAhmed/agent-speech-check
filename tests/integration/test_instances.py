"""Named instances: one asc per agent, each with its own config, daemon and NVDA."""

from __future__ import annotations

import pytest
from tests import fake_nvda as f
from tests.integration.conftest import (
    Env,
    free_port,
    run_asc,
    start_asc,
    wait_until,
    worker_panic_key,
)

from agent_speech_check.config.targets import Target
from agent_speech_check.daemon import session
from agent_speech_check.inputs.keys import parse_hotkey

# Nothing listens on port 1, so ssh fails at once.
UNREACHABLE = "[remote]\nos = 'windows'\nssh = 'ssh://me@127.0.0.1:1'\n"


@pytest.mark.windows
def test_instances_run_side_by_side(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_asc("start", "--mode", "shared", "--i-understand").returncode == 0
    # After that start, so its port is taken and free_port can't hand it out again.
    port = free_port()
    env.config(f"[shared]\nport = {port}\n", instance="vm1")
    target = Target(
        "shared",
        port,
        os="windows",
        consent=True,
        panic_key=parse_hotkey(worker_panic_key(1)),
    )
    monkeypatch.setenv("ASC_INSTANCE", "vm1")
    vm1_daemon = start_asc("_daemon", target.to_json())
    monkeypatch.delenv("ASC_INSTANCE")
    assert wait_until(lambda: session.is_running(env.folder("shared", "vm1")))
    first = env.fake("watch", {"Tab": [f.speak("first ")]})
    second = env.fake("watch", {"Tab": [f.speak("second ")]}, instance="vm1", port=port)
    try:
        assert run_asc("press", "Tab").stdout == '1 said: "first"\n'
        # The flag goes after the command too, and ASC_INSTANCE does the same.
        assert run_asc("press", "Tab", "--instance", "vm1").stdout == '1 said: "second"\n'
        # A third on vm1's port fails with a clear port-in-use error.
        env.config(f"[desktop]\nport = {port}\n", instance="vm2")
        clash = run_asc("start", "--mode", "desktop", "--instance", "vm2")
        assert clash.returncode == 3, clash.stderr
        assert "--mode desktop --instance vm2 (a free one)" in clash.stderr
        monkeypatch.setenv("ASC_INSTANCE", "vm1")
        stopped = run_asc("stop")
        assert (stopped.returncode, stopped.stdout) == (0, "stopped: instances/vm1/shared\n")
        monkeypatch.delenv("ASC_INSTANCE")
        vm1_daemon.wait(timeout=15)
        # The default instance runs on.
        assert run_asc("status").returncode == 0
        # status --all lists running and stopped instances side by side.
        all_status = run_asc("status", "--all")
        assert all_status.returncode == 0, all_status.stderr
        assert all_status.stdout.splitlines() == [
            "default: shared, running, nvda: connected",
            "vm1: shared, not running",
            "vm2: desktop, not running",
        ]
    finally:
        first.close()
        second.close()
        if vm1_daemon.poll() is None:
            vm1_daemon.kill()
            vm1_daemon.wait(timeout=5)


@pytest.mark.parametrize("mode", ["watch", pytest.param("shared", marks=pytest.mark.windows)])
def test_each_mode_runs_in_its_kind_of_instance(env: Env, mode: str) -> None:
    result = run_asc("start", "--mode", mode, "--i-understand", "--instance", "vm1")
    assert result.returncode == 2
    assert "only the default instance may join" in result.stderr
    env.config(UNREACHABLE)
    result = run_asc("start", "--mode", "remote")
    assert result.returncode == 2, result.stderr
    assert "remote mode runs only in a named instance, one per VM" in result.stderr
    assert result.stderr.endswith(
        "hint: asc start --mode remote --instance VMNAME (asc status --all lists instances)\n"
    )


def test_remote_mode_needs_a_machine_that_answers(env: Env) -> None:
    env.config("[remote]\nos = 'windows'\n", instance="vm2")
    result = run_asc("start", "--mode", "remote", "--instance", "vm2")
    assert result.returncode == 2, result.stderr
    assert "remote mode needs ssh" in result.stderr
    env.config(UNREACHABLE, instance="vm2")
    result = run_asc("start", "--mode", "remote", "--instance", "vm2")
    assert result.returncode == 3, result.stderr
    assert "ssh ssh://me@127.0.0.1:1 failed: " in result.stderr
    assert "Connection refused" in result.stderr
    assert run_asc("status", "--instance", "vm2").returncode == 3


@pytest.mark.usefixtures("env")
def test_bad_instance_names_are_refused_before_anything() -> None:
    result = run_asc("--instance", "..", "stop")
    assert result.returncode == 2
    assert 'instance "..": expected' in result.stderr
