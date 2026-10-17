"""Desktop-mode commands through the real CLI: type, launch, keys, and desktop-mode refusals.

Nothing here starts a real NVDA. Tests that need "no NVDA running" or "NVDA
running" check the machine and skip otherwise, so a developer's own NVDA is
never touched and a clean CI machine never launches one.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests import fake_nvda as f
from tests.fake_nvda import FakeNvda
from tests.integration.conftest import Env, run_asc, start_asc

from agent_speech_check.windows import winapi

FIXTURE = Path(__file__).parents[1] / "fixtures" / "keyCommands.html"
NVDA_RUNNING = bool(winapi.process_ids("nvda.exe"))


@pytest.fixture
def nvda(env: Env) -> Iterator[FakeNvda]:
    assert run_asc("start", "--target", "d").returncode == 0
    fake = env.fake("d")
    yield fake
    fake.close()


def test_type_checks_every_character_then_types(nvda: FakeNvda) -> None:
    nvda.replies["Shift+H"] = f.SILENCE
    nvda.replies["I"] = [f.speak("i")]
    typed = run_asc("type", "Hi", "--target", "d")
    assert typed.returncode == 0, typed.stderr
    assert typed.stdout == 'said: "i"\n'
    assert nvda.gestures == ["Shift+H", "I"]
    assert nvda.held == set()

    emoji = chr(0x1F600)
    bad = run_asc("type", f"ok {emoji}", "--target", "d")
    assert bad.returncode == 2
    assert "cannot type these characters" in bad.stderr
    assert nvda.gestures == ["Shift+H", "I"]


@pytest.mark.usefixtures("env")
def test_type_refused_in_watch_mode() -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    refused = run_asc("type", "x", "--target", "w")
    assert refused.returncode == 4


@pytest.mark.xdist_group("desktop")
def test_launch_waits_for_the_title(env: Env, nvda: FakeNvda) -> None:
    app = f'"{sys.executable}" -c "import time; time.sleep(3)"'
    launching = start_asc("launch", app, "--title", "My App", "--target", "d")
    env.wait_running("d", "launch")
    nvda.say("Loading")
    nvda.say("My App window")
    out, _ = launching.communicate(timeout=30)
    assert launching.returncode == 0
    assert out.splitlines() == ['said: "Loading"', 'said: "My App window"']


@pytest.mark.xdist_group("desktop")
def test_launch_errors(nvda: FakeNvda) -> None:
    missing = run_asc("launch", "no-such-program-asc.exe", "--target", "d")
    assert missing.returncode == 2
    assert 'cannot start "no-such-program-asc.exe"' in missing.stderr
    app = f'"{sys.executable}" -c pass'
    silent = run_asc("launch", app, "--title", "Never", "--timeout", "300", "--target", "d")
    assert silent.returncode == 1
    assert 'no speech matched "Never" within 300 ms' in silent.stderr
    assert "New windows: (none)" in silent.stderr
    assert nvda.held == set()


def test_keys_for_desktop_target_reads_the_portable_copy(env: Env, tmp_path: Path) -> None:
    portable = tmp_path / "portable"
    docs = portable / "documentation" / "en"
    docs.mkdir(parents=True)
    shutil.copy(FIXTURE, docs / "keyCommands.html")
    (env.root / "agent-speech-check" / "config.toml").write_text(
        f"[targets.p]\nmode = 'desktop'\nnvda_dir = '{portable.as_posix()}'\n"
        "[targets.none]\nmode = 'desktop'\nnvda_dir = 'C:/no/such/nvda'\n",
        encoding="utf-8",
    )
    found = run_asc("keys", "report", "title", "--target", "p")
    assert found.returncode == 0
    assert found.stdout.startswith("NVDA+t  Report title: ")
    assert run_asc("keys", "zzzz", "--target", "p").stdout == "(no match)\n"
    missing = run_asc("keys", "x", "--target", "none")
    assert missing.returncode == 7
    assert "the portable NVDA" in missing.stderr
    assert "hint: check nvda_dir" in missing.stderr


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("command", ["start", "keys"])
def test_nvda_dir_is_a_setting_not_a_flag(command: str, tmp_path: Path) -> None:
    result = run_asc(command, "--nvda-dir", str(tmp_path))
    assert result.returncode == 2
    assert "unrecognized arguments: --nvda-dir" in result.stderr


@pytest.mark.usefixtures("env")
def test_settings_only_for_a_launched_nvda() -> None:
    result = run_asc("start", "--target", "w", "--nvda-setting", "speech.rate=50")
    assert result.returncode == 2
    assert "only an NVDA that asc launches takes settings" in result.stderr
    bad = run_asc("start", "--mode", "desktop", "--target", "x", "--nvda-setting", "remote.k=1")
    assert bad.returncode == 2
    assert "asc sets this itself" in bad.stderr


@pytest.mark.skipif(not NVDA_RUNNING, reason="needs an NVDA running on this machine")
@pytest.mark.usefixtures("env")
def test_desktop_and_get_nvda_refuse_while_nvda_runs(tmp_path: Path) -> None:
    (tmp_path / "nvda.exe").write_bytes(b"")
    (tmp_path / "agent-speech-check" / "config.toml").write_text(
        f"[targets.x]\nmode = 'desktop'\nnvda_dir = '{tmp_path.as_posix()}'\n",
        encoding="utf-8",
    )
    started = run_asc("start", "--target", "x")
    assert started.returncode == 4
    assert "nvda.exe is already running" in started.stderr
    fetched = run_asc("get-nvda", "--installer", str(tmp_path / "nvda.exe"))
    assert fetched.returncode == 4


@pytest.mark.usefixtures("env")
def test_desktop_without_portable_nvda(tmp_path: Path) -> None:
    result = run_asc("start", "--mode", "desktop", "--target", "x")
    assert result.returncode == 7
    assert "NVDA not found" in result.stderr
    assert "hint: asc get-nvda" in result.stderr
    assert str(tmp_path) in result.stderr


def test_sandbox_needs_the_firewall_rule(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    # The wsb check only looks for the command, so an empty one stands in on machines
    # without Windows Sandbox. It's never run: the firewall check fails first.
    bin_dir = env.root / "bin"
    bin_dir.mkdir()
    (bin_dir / "wsb.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    # A port no rule covers, so this never starts a real sandbox.
    (env.root / "agent-speech-check" / "config.toml").write_text(
        f"[targets.s]\nmode = 'sandbox'\nport = {env.port}\n", encoding="utf-8"
    )
    result = run_asc("start", "--target", "s")
    assert result.returncode == 7
    assert "the firewall rule that lets the sandbox reach asc is missing" in result.stderr
    assert "hint: asc sandbox-setup" in result.stderr


def test_keys_for_sandbox_target_reads_your_own_nvda(env: Env) -> None:
    # Sandbox mode makes its portable NVDA inside the sandbox, never on the host.
    (env.root / "agent-speech-check" / "config.toml").write_text(
        "[targets.s]\nmode = 'sandbox'\n", encoding="utf-8"
    )
    found = run_asc("keys", "x", "--target", "s")
    assert "the portable NVDA" not in found.stderr


@pytest.mark.usefixtures("env")
def test_settings_are_checked_together_before_anything_starts() -> None:
    result = run_asc(
        "start", "--mode", "desktop", "--target", "x", "--nvda-setting", "speech.synth.rate=1"
    )
    assert result.returncode == 2, result.stderr
    assert "synth is a key, not a section" in result.stderr


def test_start_nvda_dir(env: Env, tmp_path: Path) -> None:
    missing = tmp_path / "none"
    broken = tmp_path / "broken"
    (env.root / "agent-speech-check" / "config.toml").write_text(
        f"[targets.missing]\nmode = 'desktop'\nnvda_dir = '{missing.as_posix()}'\n"
        f"[targets.broken]\nmode = 'desktop'\nnvda_dir = '{broken.as_posix()}'\n",
        encoding="utf-8",
    )
    desktop = run_asc("start", "--target", "missing")
    assert desktop.returncode == 7
    assert f"NVDA not found. Looked in: the portable NVDA ({missing})" in desktop.stderr
    assert "hint: check nvda_dir" in desktop.stderr
    (broken / "userConfig").mkdir(parents=True)
    (broken / "nvda.exe").write_bytes(b"")
    (broken / "userConfig" / "nvda.ini").write_text("[speech]\nnot a line\n", encoding="utf-8")
    unreadable = run_asc("start", "--target", "broken")
    assert unreadable.returncode == 2
    assert "cannot read your NVDA config:" in unreadable.stderr
    assert "nvda.ini line 2" in unreadable.stderr
