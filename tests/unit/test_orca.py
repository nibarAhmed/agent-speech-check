import asyncio
import re
from pathlib import Path

import pytest

from agent_speech_check.config.targets import Target
from agent_speech_check.errors import (
    OrcaBusy,
    OrcaNotConnected,
    OrcaSetupNeeded,
    ProtocolError,
    SshMissing,
    UntypableText,
)
from agent_speech_check.inputs.keyboard import Keyboard
from agent_speech_check.inputs.keys import KEYS, parse_gesture, typed_gestures
from agent_speech_check.orca import helper, link
from agent_speech_check.orca.link import (
    Done,
    Failed,
    OrcaLink,
    Ready,
    Record,
    keysym,
    parse_message,
)
from agent_speech_check.remote.speech_log import SpeechLog


@pytest.mark.parametrize(
    ("gesture", "keysyms"),
    [
        ("Orca+T", ["Insert", "t"]),
        ("NVDA+Shift+Numpad5", ["Insert", "Shift_L", "KP_Begin"]),
        ("Control+Alt+Windows+F5", ["Control_L", "Alt_L", "Super_L", "F5"]),
        ("Enter", ["Return"]),
        ("PageDown", ["Next"]),
        ("Equals", ["equal"]),
        ("7", ["7"]),
    ],
)
def test_keysyms(gesture: str, keysyms: list[str]) -> None:
    assert [keysym(key) for key in parse_gesture(gesture).strokes] == keysyms


def test_every_named_key_has_its_own_keysym() -> None:
    """An NVDA key name is not an X keysym: a named key left out of the table would
    reach xdotool as a name it doesn't know."""
    for key in KEYS.values():
        if len(key.name) > 1 and not re.fullmatch(r"F\d+", key.name):
            assert key.name in link.KEYSYMS


def test_typed_text_needs_no_keyboard_layout() -> None:
    gestures = typed_gestures("aB!é\r\n\t")
    assert [keysym(g.key) for g in gestures] == [
        "U0061",
        "U0042",
        "U0021",
        "U00E9",
        "Return",
        "Tab",
    ]
    assert all(not g.modifiers for g in gestures)


def test_control_characters_are_not_typed() -> None:
    with pytest.raises(UntypableText):
        typed_gestures("a\x07b")


def test_helper_command_local_and_over_ssh() -> None:
    local = link.helper_command(None)
    assert local[1:] == ["-I", "-c", link.BOOTSTRAP]
    remote = link.helper_command("me@vm")
    assert remote[:-1] == [
        *("ssh", "-T", "-o", "BatchMode=yes"),
        *("-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"),
        *("--", "me@vm"),
    ]
    assert remote[-1] == f"python3 -I -c '{link.BOOTSTRAP}'"
    # Windows passes it on one command line: nothing in it needs escaping there.
    assert not set('"\\') & set(link.BOOTSTRAP)


def test_payload_is_what_the_bootstrap_reads() -> None:
    length, script = link.payload().split(b"\n", 1)
    assert int(length) == len(script)
    assert script == Path(helper.__file__).read_bytes()


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (b'{"ready": "51.1"}', Ready("51.1")),
        (b'{"record": {"kind": "speech", "text": "OK button"}}', Record("speech", "OK button")),
        (b'{"record": {"kind": "interrupt"}}', Record("interrupt", None)),
        (b'{"done": 3}', Done(3, None)),
        (b'{"done": 4, "error": "no such file"}', Done(4, "no such file")),
        # A failed launch names its command, up to ipc's 8000 characters.
        (b'{"done": 5, "error": "' + b"x" * 9000 + b'"}', Done(5, "x" * 2000)),
        (b'{"failed": "setup", "reason": "missing xdotool"}', OrcaSetupNeeded),
        (b'{"failed": "busy", "reason": "Orca runs"}', OrcaBusy),
    ],
)
def test_helper_messages(line: bytes, message: object) -> None:
    got = parse_message(line)
    if isinstance(message, type):
        assert isinstance(got, Failed)
        assert isinstance(got.error, message)
    else:
        assert got == message


@pytest.mark.parametrize(
    "line",
    [b"not json", b"[]", b'{"record": {"kind": "speech", "text": 5}}', b'{"failed": "why"}'],
)
def test_bad_helper_messages(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_message(line)


def test_unknown_helper_message_is_skipped() -> None:
    assert parse_message(b'{"later": 1}') is None


def test_orca_version() -> None:
    assert helper.orca_version("Orca version 51.1, AT-SPI2 version: 2.62.1") == (51, 1)
    assert helper.orca_version("command not found") is None


def test_another_orca_of_the_same_user(tmp_path: Path) -> None:
    (tmp_path / "self").mkdir()  # not a pid: skipped
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "comm").write_text("bash\n")
    assert not helper.others_running((tmp_path / "41").stat().st_uid, tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "comm").write_text("orca\n")
    uid = (tmp_path / "42").stat().st_uid
    assert helper.others_running(uid, tmp_path)
    assert not helper.others_running(uid + 1, tmp_path)


def test_folders_of_killed_helpers_are_removed(tmp_path: Path) -> None:
    proc, temp = tmp_path / "proc", tmp_path / "tmp"
    (proc / "41").mkdir(parents=True)
    for name, pid in [("asc-orca-live", "41"), ("asc-orca-dead", "42"), ("asc-orca-new", None)]:
        (temp / name / "home").mkdir(parents=True)
        if pid is not None:
            (temp / name / "pid").write_text(pid)
    (temp / "other").mkdir()
    uid = temp.stat().st_uid
    helper.remove_stale(temp, uid + 1, proc)
    assert (temp / "asc-orca-dead").exists()
    helper.remove_stale(temp, uid, proc)
    # A helper that hasn't written its pid yet may be starting.
    assert sorted(p.name for p in temp.iterdir()) == ["asc-orca-live", "asc-orca-new", "other"]


def test_no_ssh_on_this_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    speech = SpeechLog()
    orca = OrcaLink("me@vm", speech)
    asyncio.run(orca.run())
    assert isinstance(orca.failure, SshMissing)
    keyboard = Keyboard(Target("orca", 0, launch=False, ssh="me@vm"), orca, speech)
    with pytest.raises(OrcaNotConnected):
        asyncio.run(keyboard.press((parse_gesture("Tab"),), 0, 1))
    with pytest.raises(OrcaNotConnected):
        orca.write_key(KEYS["tab"], pressed=True)
