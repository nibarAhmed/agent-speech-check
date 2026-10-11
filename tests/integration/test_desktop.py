"""M2 commands through the real CLI: type, launch, keys, and desktop-mode refusals.

Nothing here starts a real NVDA. Tests that need "no NVDA running" or "NVDA
running" check the machine and skip otherwise, so a developer's own NVDA is
never touched and a clean CI machine never launches one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests import fake_nvda as f
from tests.fake_nvda import FakeNvda
from tests.integration.conftest import Env, run_asc

from agent_speech_check import winapi

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


def test_launch_waits_for_the_title(nvda: FakeNvda) -> None:
    app = f'"{sys.executable}" -c "import time; time.sleep(3)"'
    launching = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [
            sys.executable,
            "-m",
            "agent_speech_check",
            "launch",
            app,
            "--title",
            "My App",
            "--target",
            "d",
        ],
        stdout=subprocess.PIPE,
        encoding="utf-8",
    )
    time.sleep(1)
    nvda.say("Loading")
    nvda.say("My App window")
    out, _ = launching.communicate(timeout=30)
    assert launching.returncode == 0
    assert out.splitlines() == ['said: "Loading"', 'said: "My App window"']


def test_launch_errors(nvda: FakeNvda) -> None:
    missing = run_asc("launch", "no-such-program-asc.exe", "--target", "d")
    assert missing.returncode == 2
    assert 'cannot start "no-such-program-asc.exe"' in missing.stderr
    app = f'"{sys.executable}" -c pass'
    silent = run_asc("launch", app, "--title", "Never", "--timeout", "300", "--target", "d")
    assert silent.returncode == 1
    assert 'no speech matched "Never" within 300 ms' in silent.stderr
    assert nvda.held == set()


def test_keys_from_a_given_nvda(tmp_path: Path) -> None:
    docs = tmp_path / "nvda" / "documentation" / "en"
    docs.mkdir(parents=True)
    shutil.copy(FIXTURE, docs / "keyCommands.html")
    nvda_dir = str(tmp_path / "nvda")
    found = run_asc("keys", "report", "title", "--nvda-dir", nvda_dir)
    assert found.returncode == 0
    assert found.stdout.startswith("NVDA+t  Report title: ")
    assert run_asc("keys", "zzzz", "--nvda-dir", nvda_dir).stdout == "(no match)\n"
    missing = run_asc("keys", "x", "--nvda-dir", str(tmp_path / "none"))
    assert missing.returncode == 3
    assert "NVDA not found. Looked in: --nvda-dir" in missing.stderr


def test_keys_for_desktop_target_reads_the_portable_copy(env: Env, tmp_path: Path) -> None:
    portable = tmp_path / "portable"
    (env.root / "agent-speech-check" / "targets.toml").write_text(
        f"[targets.p]\nmode = 'desktop'\nnvda_dir = '{portable.as_posix()}'\n",
        encoding="utf-8",
    )
    missing = run_asc("keys", "x", "--target", "p")
    assert missing.returncode == 3
    assert "the portable NVDA" in missing.stderr


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
    (tmp_path / "agent-speech-check" / "targets.toml").write_text(
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
    assert result.returncode == 3
    assert "NVDA not found" in result.stderr
    assert "hint: asc get-nvda" in result.stderr
    assert str(tmp_path) in result.stderr


def test_sandbox_needs_the_firewall_rule(env: Env) -> None:
    # A port no rule covers, so this never starts a real sandbox.
    (env.root / "agent-speech-check" / "targets.toml").write_text(
        f"[targets.s]\nmode = 'sandbox'\nport = {env.port}\n", encoding="utf-8"
    )
    result = run_asc("start", "--target", "s")
    assert result.returncode == 3
    assert "the firewall rule that lets the sandbox reach asc is missing" in result.stderr
    assert "hint: asc sandbox-setup" in result.stderr


def test_keys_for_sandbox_target_reads_your_own_nvda(env: Env) -> None:
    # Sandbox mode makes its portable NVDA inside the sandbox, never on the host.
    (env.root / "agent-speech-check" / "targets.toml").write_text(
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
