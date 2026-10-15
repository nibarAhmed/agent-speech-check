"""Watch mode end to end: real CLI, real daemon process, fake NVDA over real TLS."""

from __future__ import annotations

import json

import pytest
from tests.integration.conftest import Env, run_asc

from agent_speech_check.daemon import server
from agent_speech_check.windows import winapi


def test_watch_session(env: Env) -> None:
    started = run_asc("start", "--target", "w")
    assert started.returncode == 0, started.stderr
    assert "started: w" in started.stdout
    assert "w (watch mode, keys refused)" in started.stdout
    assert "recording: yes" in started.stdout
    assert "nvda: not connected" in started.stdout
    assert f"host 127.0.0.1:{env.port}, key " in started.stdout

    assert run_asc("speech", "--target", "w").stdout == "(nothing said)\n"
    not_connected = run_asc("wait", "x", "--target", "w")
    assert not_connected.returncode == 3
    assert "NVDA is not connected" in not_connected.stderr

    fake = env.fake("w")
    try:
        fake.say("Recycle Bin ", "list item ", "focused ")
        waited = run_asc("wait", "Recycle", "--target", "w")
        assert waited.returncode == 0, waited.stderr
        assert waited.stdout == '1 said: "Recycle Bin list item focused"\n'

        assert run_asc("mark", "--target", "w").stdout == "mark: 1\n"
        fake.say("second")
        assert run_asc("wait", "second", "--target", "w").returncode == 0
        assert run_asc("speech", "--target", "w").stdout == '2 said: "second"\n'
        everything = run_asc("speech", "--since", "0", "--target", "w").stdout
        assert everything.splitlines() == [
            '1 said: "Recycle Bin list item focused"',
            '2 said: "second"',
        ]

        status = run_asc("status", "--target", "w")
        assert "nvda: connected" in status.stdout
        assert "browse/focus: unknown" in status.stdout

        refused = run_asc("press", "Tab", "--target", "w")
        assert refused.returncode == 4
        assert "watch mode, which never sends keys" in refused.stderr
        assert not any(m.get("type") == "key" for m in fake.received)

        missed = run_asc("wait", "nope", "--timeout", "200", "--target", "w")
        assert missed.returncode == 1
        assert 'no speech matched "nope" within 200 ms' in missed.stderr
        assert 'said: "second"' in missed.stderr

        assert run_asc("clear", "--target", "w").stdout == "cleared\n"
        assert run_asc("speech", "--target", "w").stdout == "(nothing said)\n"
    finally:
        fake.close()

    stopped = run_asc("stop", "--target", "w")
    assert stopped.returncode == 0
    assert stopped.stdout == "stopped: w\n"
    gone = run_asc("status", "--target", "w")
    assert gone.returncode == 3
    assert "not running" in gone.stderr
    assert "not recording" in gone.stderr


# Built with chr() so linters don't flag look-alike characters in the source.
SAMPLE = f"Caf{chr(0xE9)} {chr(0x2019)}q{chr(0x2019)} {chr(0x2026)} {chr(0x4E2D)} {chr(0x1F600)}"


def test_any_unicode_speech_prints_as_utf8(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    fake = env.fake("w")
    try:
        fake.say(SAMPLE)
        waited = run_asc("wait", SAMPLE[:4], "--target", "w")
        assert waited.returncode == 0, waited.stderr
        assert waited.stdout == f'1 said: "{SAMPLE}"' + chr(10)
    finally:
        fake.close()


def test_cert_and_key_stay_the_same(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    folder = env.folder("w")
    first = (folder / "relay_cert.pem").read_bytes(), (folder / "relay.json").read_text()
    assert run_asc("stop", "--target", "w").returncode == 0
    assert run_asc("start", "--target", "w").returncode == 0
    assert ((folder / "relay_cert.pem").read_bytes(), (folder / "relay.json").read_text()) == first


@pytest.mark.usefixtures("env")
def test_second_start_is_refused() -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    again = run_asc("start", "--target", "w")
    assert again.returncode == 4
    assert 'asc is already running for target "w"' in again.stderr
    assert "hint: asc stop --target w" in again.stderr


def test_stale_state_file_is_removed(env: Env) -> None:
    folder = env.folder("w")
    folder.mkdir(parents=True)
    state = {"pid": 1, "port": env.port, "token": "x", "started": 0}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    result = run_asc("status", "--target", "w")
    assert result.returncode == 3
    assert "not running" in result.stderr
    assert not (folder / "state.json").exists()


def test_daemon_that_cannot_bind_reports_its_log(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    # Same relay port, another target: the second daemon can't listen.
    (env.root / "agent-speech-check" / "config.toml").write_text(
        f'[targets.w]\nmode = "watch"\nport = {env.port}\n'
        f'[targets.w2]\nmode = "watch"\nport = {env.port}\n',
        encoding="utf-8",
    )
    failed = run_asc("start", "--target", "w2")
    assert failed.returncode == 3
    assert "the asc daemon did not start: it exited with code" in failed.stderr
    assert "could not start" in failed.stderr


@pytest.mark.usefixtures("env")
def test_shared_mode_needs_consent() -> None:
    result = run_asc("start", "--mode", "shared", "--target", "s")
    assert result.returncode == 4
    assert "sends keys into your own Windows session" in result.stderr
    assert "--i-understand" in result.stderr


@pytest.mark.xdist_group("desktop")
@pytest.mark.usefixtures("env")
def test_shared_mode_with_the_panic_key_taken_exits_4(env: Env) -> None:
    (env.root / "agent-speech-check" / "config.toml").write_text(
        f"[targets.sh]\nmode = 'shared'\nport = {env.port}\n", encoding="utf-8"
    )
    holder = winapi.GlobalHotkey(server.PANIC_MODIFIERS, server.PANIC_VK, lambda: None)
    if not holder.start():
        pytest.skip("another program owns the panic hotkey")
    try:
        result = run_asc("start", "--target", "sh", "--i-understand")
    finally:
        holder.stop()
    assert result.returncode == 4, result.stderr
    assert "cannot register the panic hotkey" in result.stderr
