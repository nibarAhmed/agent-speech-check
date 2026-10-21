"""Keys end to end, and every fault fake_nvda can script. No key may stay held.

Desktop mode has `launch = false` here: NVDA (here the fake)
connects by itself, so keys are allowed without launching a real NVDA.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from tests import fake_nvda as f
from tests.fake_nvda import FakeNvda
from tests.integration.conftest import Env, run_asc, start_asc, wait_until

from agent_speech_check.remote.speech_log import CAP


@pytest.fixture
def nvda(env: Env) -> Iterator[FakeNvda]:
    started = run_asc("start", "--mode", "desktop")
    assert started.returncode == 0, started.stderr
    assert "mode: desktop (keys allowed)" in started.stdout
    # launch = false: the test starts NVDA (the fake, which takes no real keys).
    assert "panic key" not in started.stdout
    fake = env.fake("desktop")
    yield fake
    fake.close()


def wait_rejoined(fake: FakeNvda, joins: int) -> None:
    assert wait_until(lambda: fake.joins > joins), fake.failure
    # The relay sends its releases after channel_joined. Keys stay held across a drop,
    # so only those releases empty `held`.
    assert wait_until(lambda: not fake.held), fake.held


def test_press_prints_new_speech_and_releases(nvda: FakeNvda) -> None:
    nvda.replies["NVDA+T"] = [f.CANCEL, f.speak("Program Manager ")]
    result = run_asc("press", "NVDA+T")
    assert result.returncode == 0, result.stderr
    assert result.stdout == '1 said: "Program Manager"\n'
    assert nvda.gestures == ["NVDA+T"]
    assert nvda.held == set()
    keys = [(m["vk_code"], m["pressed"]) for m in nvda.received if m["type"] == "key"]
    assert keys == [(0x2D, True), (0x54, True), (0x54, False), (0x2D, False)]


def test_several_gestures_and_mode_line(nvda: FakeNvda) -> None:
    nvda.replies["Tab"] = [f.speak("Your name ", "edit ", "blank "), f.wave("focusMode.wav")]
    nvda.replies["Escape"] = [f.wave("browseMode.wav")]
    result = run_asc("press", "Tab", "Esc")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        '1 said: "Your name edit blank"',
        "2 mode: focus",
        "3 mode: browse",
    ]
    assert "browse/focus: browse" in run_asc("status").stdout


def test_expect(nvda: FakeNvda) -> None:
    nvda.replies["H"] = [f.speak("Second heading ", "heading ", "level 2")]
    ok = run_asc("press", "H", "--expect", "level 2")
    assert ok.returncode == 0
    nvda.replies["H"] = [f.speak("no next heading")]
    failed = run_asc("press", "H", "--expect", "level 3", "--timeout", "800")
    assert failed.returncode == 1
    assert 'no speech matched "level 3" within 800 ms' in failed.stderr
    assert 'NVDA said:\n2 said: "no next heading"' in failed.stderr
    assert nvda.held == set()


def _flooded_press(nvda: FakeNvda, *args: str) -> tuple[int, str, str]:
    """Press H, then say five lines more than the log keeps while the press settles."""
    nvda.replies["H"] = f.SILENCE
    nvda.gestures.clear()
    # The settle outlasts the flood, so every line comes after the key.
    press = start_asc("press", "H", "--settle", "3000", *args)
    assert wait_until(lambda: bool(nvda.gestures)), "the press never reached NVDA"
    for n in range(1, CAP + 6):
        nvda.send(f.speak(f"line {n}"))
    out, err = press.communicate(timeout=60)
    return press.returncode, out, err


def test_a_press_that_outruns_the_log_says_lines_were_dropped(nvda: FakeNvda) -> None:
    code, _, err = _flooded_press(nvda, "--expect", "nowhere", "--timeout", "6000")
    assert code == 1, err
    assert 'NVDA said:\n(5 earlier lines dropped)\n6 said: "line 6"\n' in err
    code, out, err = _flooded_press(nvda)
    assert code == 0, err
    assert out.startswith(f'(5 earlier lines dropped)\n{CAP + 11} said: "line 6"\n')


def test_silence_names_the_line_to_read_after(nvda: FakeNvda) -> None:
    nvda.replies["Space"] = f.SILENCE
    result = run_asc("press", "Space")
    assert result.returncode == 0
    assert result.stdout == "(nothing said after 0)\n"
    nvda.replies["Tab"] = [f.speak("OK button")]
    assert run_asc("press", "Tab").stdout == '1 said: "OK button"\n'
    assert run_asc("press", "Space").stdout == "(nothing said after 1)\n"


def test_unknown_type_is_ignored(nvda: FakeNvda) -> None:
    nvda.replies["Down"] = f.UNKNOWN
    result = run_asc("press", "Down")
    assert result.returncode == 0
    assert result.stdout == '1 said: "after unknown"\n'


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (f.MALFORMED, "NVDA sent a line that is not a JSON object"),
        (f.OVERSIZE, "NVDA sent a line longer than 1048576 bytes"),
        (f.SERVER_ERROR, 'NVDA reported an error: "boom"'),
    ],
)
def test_protocol_faults_exit_5_and_recover(nvda: FakeNvda, fault: str, message: str) -> None:
    nvda.replies["Up"] = fault
    joins = nvda.joins
    result = run_asc("press", "Up")
    assert result.returncode == 5, result.stdout + result.stderr
    assert message in result.stderr
    wait_rejoined(nvda, joins)
    assert nvda.held == set()
    status = run_asc("status").stdout
    assert "nvda: connected" in status
    del nvda.replies["Up"]
    assert re.fullmatch(r'[0-9]+ said: "pressed Up"\n', run_asc("press", "Up").stdout)


def test_disconnect_mid_gesture_releases_after_reconnect(nvda: FakeNvda) -> None:
    nvda.replies["Shift+Tab"] = f.DISCONNECT_MID_GESTURE
    joins = nvda.joins
    result = run_asc("press", "Shift+Tab")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "NVDA is not connected" in result.stderr
    wait_rejoined(nvda, joins)
    assert nvda.held == set()


def test_bad_input_sends_nothing(nvda: FakeNvda) -> None:
    unknown = run_asc("press", "Tab", "Escap")
    assert unknown.returncode == 2
    assert 'unknown key "Escap"' in unknown.stderr
    assert "hint: did you mean Escape" in unknown.stderr
    regex = run_asc("press", "Tab", "--expect", "(")
    assert regex.returncode == 2
    assert 'bad regex "(": missing ), unterminated subpattern at position 0' in regex.stderr
    settle = run_asc("press", "Tab", "--settle", "5")
    assert settle.returncode == 2
    assert "settle_ms: expected a whole number from 50 to 10000, got 5" in settle.stderr
    assert not any(m["type"] == "key" for m in nvda.received)


@pytest.mark.usefixtures("env")
def test_press_without_nvda() -> None:
    assert run_asc("start", "--mode", "desktop").returncode == 0
    result = run_asc("press", "Tab")
    assert result.returncode == 3
    assert "NVDA is not connected" in result.stderr


@pytest.mark.xdist_group("desktop")
@pytest.mark.usefixtures("env")
def test_shared_mode_with_consent_sends_keys_and_has_a_panic_key(env: Env) -> None:
    # Its own panic key, so it never clashes with a real asc on this machine.
    env.config(f"[defaults]\npanic_key = 'ctrl+shift+alt+f21'\n[shared]\nport = {env.port}\n")
    started = run_asc("start", "--mode", "shared", "--i-understand")
    assert started.returncode == 0, started.stderr
    assert "mode: shared (keys allowed)" in started.stdout
    assert "panic key: Control+Shift+Alt+F21 stops asc and releases keys" in started.stdout
    fake = env.fake("shared", {"Tab": [f.speak("OK button")]})
    try:
        pressed = run_asc("press", "Tab", "--expect", "OK")
        assert pressed.returncode == 0, pressed.stderr
        assert fake.held == set()
    finally:
        fake.close()
