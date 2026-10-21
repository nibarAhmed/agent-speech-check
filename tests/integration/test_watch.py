"""Watch mode end to end: real CLI, real daemon process, fake NVDA over real TLS."""

from __future__ import annotations

import json
import socket

import pytest
from tests.integration.conftest import Env, run_asc

from agent_speech_check.inputs.keys import parse_hotkey
from agent_speech_check.windows import winapi


def test_watch_session(env: Env) -> None:
    started = run_asc("start", "--mode", "watch")
    assert started.returncode == 0, started.stderr
    assert "started: watch" in started.stdout
    assert "mode: watch (keys refused)" in started.stdout
    assert "recording: yes" in started.stdout
    assert "nvda: not connected" in started.stdout
    assert f"host 127.0.0.1:{env.port}, key " in started.stdout

    assert run_asc("speech", "--since", "0").stdout == "(nothing said after 0)\n"
    # A number from an earlier session: numbering restarts with each start.
    for command in (("speech",), ("wait", "x")):
        ahead = run_asc(*command, "--since", "5")
        assert ahead.returncode == 2
        assert "--since 5 is after the latest line, 0" in ahead.stderr
        assert "hint: asc mark" in ahead.stderr
    not_connected = run_asc("wait", "x", "--since", "0")
    assert not_connected.returncode == 3
    assert "NVDA is not connected" in not_connected.stderr

    fake = env.fake("watch")
    try:
        fake.say("Recycle Bin ", "list item ", "focused ")
        waited = run_asc("wait", "Recycle", "--since", "0")
        assert waited.returncode == 0, waited.stderr
        assert waited.stdout == '1 said: "Recycle Bin list item focused"\n'

        assert run_asc("mark").stdout == "mark: 1\n"
        fake.say("second")
        assert run_asc("wait", "second", "--since", "1").returncode == 0
        assert run_asc("speech", "--since", "1").stdout == '2 said: "second"\n'
        everything = run_asc("speech", "--since", "0").stdout
        assert everything.splitlines() == [
            '1 said: "Recycle Bin list item focused"',
            '2 said: "second"',
        ]

        status = run_asc("status")
        assert "nvda: connected" in status.stdout
        assert "browse/focus: unknown" in status.stdout
        assert "speech: 2 lines kept, latest 2\n" in status.stdout

        refused = run_asc("press", "Tab")
        assert refused.returncode == 4
        assert "watch mode, which never sends keys" in refused.stderr
        assert not any(m.get("type") == "key" for m in fake.received)

        missed = run_asc("wait", "nope", "--since", "1", "--timeout", "200")
        assert missed.returncode == 1
        assert 'no speech matched "nope" within 200 ms' in missed.stderr
        assert 'NVDA said:\n2 said: "second"' in missed.stderr

    finally:
        fake.close()

    stopped = run_asc("stop")
    assert stopped.returncode == 0
    assert stopped.stdout == "stopped: watch\n"
    gone = run_asc("status")
    assert gone.returncode == 3
    assert "not running" in gone.stderr
    assert "not recording" in gone.stderr


# Built with chr() so linters don't flag look-alike characters in the source.
SAMPLE = f"Caf{chr(0xE9)} {chr(0x2019)}q{chr(0x2019)} {chr(0x2026)} {chr(0x4E2D)} {chr(0x1F600)}"


def test_any_unicode_speech_prints_as_utf8(env: Env) -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    fake = env.fake("watch")
    try:
        fake.say(SAMPLE)
        waited = run_asc("wait", SAMPLE[:4], "--since", "0")
        assert waited.returncode == 0, waited.stderr
        assert waited.stdout == f'1 said: "{SAMPLE}"' + chr(10)
    finally:
        fake.close()


def test_cert_and_key_stay_the_same(env: Env) -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    folder = env.folder("watch")
    first = (folder / "relay_cert.pem").read_bytes(), (folder / "relay.json").read_text()
    assert run_asc("stop").returncode == 0
    assert run_asc("start", "--mode", "watch").returncode == 0
    assert ((folder / "relay_cert.pem").read_bytes(), (folder / "relay.json").read_text()) == first


@pytest.mark.usefixtures("env")
def test_second_start_is_refused() -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    again = run_asc("start", "--mode", "watch")
    assert again.returncode == 4
    assert "asc is already running" in again.stderr
    assert again.stderr.endswith("hint: asc stop, then try again\n")


def test_stale_state_file_is_removed(env: Env) -> None:
    folder = env.folder("watch")
    folder.mkdir(parents=True)
    state = {"pid": 1, "port": env.port, "token": "x", "started": 0}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    result = run_asc("status")
    assert result.returncode == 3
    assert "not running" in result.stderr
    assert not (folder / "state.json").exists()


def test_daemon_that_cannot_bind_reports_its_log(env: Env) -> None:
    # Something else holds the relay port, so the daemon can't listen.
    with socket.create_server(("127.0.0.1", env.port)):
        failed = run_asc("start", "--mode", "watch")
    assert failed.returncode == 3
    assert "the asc daemon did not start: it exited with code" in failed.stderr
    assert "could not start" in failed.stderr


def test_one_daemon_at_a_time(env: Env) -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    second = run_asc("start", "--mode", "desktop")
    assert second.returncode == 4
    assert "asc is already running" in second.stderr
    assert "hint: asc stop" in second.stderr
    assert not (env.folder("desktop") / "state.json").exists()
    assert run_asc("stop").stdout == "stopped: watch\n"
    assert run_asc("start", "--mode", "desktop").returncode == 0
    assert "mode: desktop (keys allowed)" in run_asc("status").stdout


@pytest.mark.usefixtures("env")
def test_commands_find_the_daemon_without_a_mode() -> None:
    nothing = run_asc("status")
    assert nothing.returncode == 3
    assert "asc is not running (not recording)" in nothing.stderr
    assert "hint: asc start" in nothing.stderr
    assert run_asc("stop").returncode == 3
    named = run_asc("speech", "--since", "0", "--mode", "watch")
    assert named.returncode == 2
    assert "unrecognized arguments: --mode" in named.stderr
    old = run_asc("start", "--target", "w")
    assert old.returncode == 2
    assert "unrecognized arguments: --target" in old.stderr


@pytest.mark.usefixtures("env")
def test_shared_mode_needs_consent() -> None:
    result = run_asc("start", "--mode", "shared")
    assert result.returncode == 4
    assert "sends keys into your own Windows session" in result.stderr
    assert "--i-understand" in result.stderr


@pytest.mark.xdist_group("desktop")
@pytest.mark.usefixtures("env")
def test_shared_mode_with_the_panic_key_taken_exits_4(env: Env) -> None:
    # Its own panic key, which this test holds, so a real asc on this machine can't interfere.
    env.config(f"[defaults]\npanic_key = 'ctrl+shift+alt+f22'\n[shared]\nport = {env.port}\n")
    hotkey = parse_hotkey("ctrl+shift+alt+f22")
    holder = winapi.GlobalHotkey(hotkey.modifiers, hotkey.vk, lambda: None)
    assert holder.start(), "another program owns Control+Shift+Alt+F22"
    try:
        result = run_asc("start", "--mode", "shared", "--i-understand")
    finally:
        holder.stop()
    assert result.returncode == 4, result.stderr
    assert "cannot register the panic hotkey Control+Shift+Alt+F22" in result.stderr
    assert "asc config set panic_key" in result.stderr
