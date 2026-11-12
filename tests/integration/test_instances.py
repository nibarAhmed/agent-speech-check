"""Named instances: one asc per agent, each with its own config, daemon and NVDA.

Desktop mode has `launch = false` here, so a fake NVDA stands in for each one.
"""

from __future__ import annotations

import pytest
from tests import fake_nvda as f
from tests.integration.conftest import Env, free_port, run_asc


def test_instances_run_side_by_side(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_asc("start", "--mode", "desktop").returncode == 0
    # After that start, so its port is taken and free_port can't hand it out again.
    port = free_port()
    env.config(f"[desktop]\nport = {port}\nlaunch = false\n", instance="vm1")
    started = run_asc("--instance", "vm1", "start", "--mode", "desktop")
    assert started.returncode == 0, started.stderr
    first = env.fake("desktop", {"Tab": [f.speak("first ")]})
    second = env.fake("desktop", {"Tab": [f.speak("second ")]}, instance="vm1", port=port)
    try:
        assert run_asc("press", "Tab").stdout == '1 said: "first"\n'
        # The flag goes after the command too, and ASC_INSTANCE does the same.
        assert run_asc("press", "Tab", "--instance", "vm1").stdout == '1 said: "second"\n'
        monkeypatch.setenv("ASC_INSTANCE", "vm1")
        stopped = run_asc("stop")
        assert (stopped.returncode, stopped.stdout) == (0, "stopped: instances/vm1/desktop\n")
        monkeypatch.delenv("ASC_INSTANCE")
        # The default instance runs on.
        assert run_asc("status").returncode == 0
    finally:
        first.close()
        second.close()


@pytest.mark.usefixtures("env")
def test_own_nvda_only_in_the_default_instance() -> None:
    result = run_asc("start", "--mode", "shared", "--i-understand", "--instance", "vm1")
    assert result.returncode == 4
    assert "only the default instance may join" in result.stderr


def test_remote_mode_needs_its_firewall_rule(env: Env) -> None:
    # A port no rule names. The host skips the Default Switch lookup.
    env.config(f"[remote]\nport = {free_port()}\nhost = '172.31.255.1'\n", instance="vm2")
    result = run_asc("start", "--mode", "remote", "--instance", "vm2")
    assert result.returncode == 7, result.stderr
    assert "hint: asc firewall-setup --mode remote --instance vm2" in result.stderr


@pytest.mark.usefixtures("env")
def test_bad_instance_names_are_refused_before_anything() -> None:
    result = run_asc("--instance", "..", "stop")
    assert result.returncode == 2
    assert 'instance "..": expected' in result.stderr
