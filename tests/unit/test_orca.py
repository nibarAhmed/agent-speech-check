import asyncio
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent_speech_check.config.targets import Target
from agent_speech_check.daemon.speech_log import SpeechLog
from agent_speech_check.errors import OperationalError, OrcaNotConnected, ProtocolError, UsageError
from agent_speech_check.inputs.keyboard import Keyboard
from agent_speech_check.inputs.keys import KEYS, parse_gesture, typed_gestures
from agent_speech_check.orca import helper, link
from agent_speech_check.orca.link import (
    Done,
    Failed,
    Help,
    OrcaLink,
    Ready,
    Record,
    parse_message,
)


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
    assert [key.keysym for key in parse_gesture(gesture).strokes] == keysyms


def test_typed_text_needs_no_keyboard_layout() -> None:
    gestures = typed_gestures("aB!é\r\n\t")
    assert [g.key.keysym for g in gestures] == [
        "U0061",
        "U0042",
        "U0021",
        "U00E9",
        "Return",
        "Tab",
    ]
    assert all(not g.modifiers for g in gestures)


def test_control_characters_are_not_typed() -> None:
    with pytest.raises(UsageError, match="cannot type these characters"):
        typed_gestures("a\x07b")


def test_helper_command_local_and_over_ssh() -> None:
    assert link.helper_command(None) == ["python3", "-I", "-c", link.BOOTSTRAP]
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
    length, rest = link.payload({"watch": "s3cret"}).split(b"\n", 1)
    assert rest[: int(length)] == Path(helper.__file__).read_bytes()
    assert rest[int(length) :] == b'{"watch": "s3cret"}\n'
    assert link.payload({}).endswith(b"\n{}\n")


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
        (b'{"failed": "setup", "reason": "missing xdotool"}', ("missing xdotool", "install Orca")),
        (b'{"failed": "busy", "reason": "Orca runs"}', ("Orca runs", "close that Orca")),
        (b'{"failed": "watch", "reason": "no secret"}', ("no secret", "restart Orca")),
        (b'{"failed": "help", "reason": "not found"}', ("not found", "help readable")),
        (b'{"help": {"/a/commands_a.page": ""}}', Help({"/a/commands_a.page": ""})),
    ],
)
def test_helper_messages(line: bytes, message: object) -> None:
    got = parse_message(line)
    if isinstance(message, tuple):
        text, hint = message
        assert isinstance(got, Failed)
        assert isinstance(got.error, OperationalError)
        assert text in got.error.message
        assert hint in (got.error.hint or "")
    else:
        assert got == message


@pytest.mark.parametrize(
    "line", [b"not json", b"[]", b'{"done": "x"}', b'{"failed": "why"}', b'{"help": {"/a": 1}}']
)
def test_bad_helper_messages(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_message(line)


# The helper passes Orca's records on as Orca wrote them: one asc can't read is skipped.
@pytest.mark.parametrize(
    "line",
    [
        b'{"later": 1}',
        b'{"record": {"kind": "speech", "text": 5}}',
        b'{"record": {"text": "OK"}}',
        b'{"record": ["speech"]}',
    ],
)
def test_unknown_helper_message_is_skipped(line: bytes) -> None:
    assert parse_message(line) is None


def test_taking_records_in_never_waits_for_them_to_go_out() -> None:
    # An Orca whose recording pipe fills stops: in watch mode, the user's own.
    records = helper.Records(limit=3)
    records.drain(io.BytesIO(b"".join(b"%d\n" % n for n in range(10))))
    assert list(iter(records.take, None)) == [b"7\n", b"8\n", b"9\n"]


def test_watch_secret_is_made_once_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "state" / "orca-secret"
    secret = link.watch_secret(path)
    assert len(secret) == 32
    assert link.watch_secret(path) == secret


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


def test_children_of_a_process(tmp_path: Path) -> None:
    (tmp_path / "self").mkdir()  # not a pid: skipped
    for pid, stat in [
        ("10", "10 (bash) S 1 10"),
        ("11", "11 (a) b) S 7 11"),
        ("12", "12 (x) Z 7 1"),
    ]:
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "stat").write_text(stat)
    assert sorted(helper.children_of(7, tmp_path)) == [11, 12]


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


def test_a_wait_fails_once_orca_is_gone() -> None:
    speech = SpeechLog()
    orca = OrcaLink(None, speech)
    orca._dispatch(Ready("51.1"))
    orca.check(0, drop_fails=False)
    orca._ended(0, None)
    check = lambda: orca.check(0, drop_fails=False)  # noqa: E731
    with pytest.raises(OrcaNotConnected):
        asyncio.run(speech.wait_match(re.compile("hello"), 0, 60, check))


def test_a_helper_that_stops_answering_is_a_lost_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(link, "ACK_TIMEOUT_S", 0.2)
    hangs = "import sys; sys.stdin.buffer.read()"  # the peer under test never answers

    async def scenario() -> OrcaLink:
        speech = SpeechLog()
        orca = OrcaLink(None, speech)
        keyboard = Keyboard(Target("desktop", 0, os="linux"), orca, speech)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", hangs, stdin=subprocess.PIPE
        )
        orca._process, orca._stdin = process, process.stdin
        orca._dispatch(Ready("51.1"))
        both = keyboard.press((parse_gesture("Tab"),), 0, 5), orca.launch("gedit")
        lost = await asyncio.gather(*both, return_exceptions=True)
        assert all(isinstance(error, OrcaNotConnected) for error in lost)
        assert not orca.connected  # as asc status says
        keyboard.release_all()  # the Tab it may still hold, with Orca gone
        orca._ended(await process.wait(), None)
        return orca

    orca = asyncio.run(scenario())
    assert orca.failure is None
    assert orca.losses == [None]
    assert orca.last_error is not None
    assert "did not answer" in orca.last_error


def test_no_ssh_on_this_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    speech = SpeechLog()
    orca = OrcaLink("me@vm", speech)
    asyncio.run(orca.run())
    assert isinstance(orca.failure, OperationalError)
    assert "cannot run ssh" in orca.failure.message
    keyboard = Keyboard(Target("remote", 0, ssh="me@vm", os="linux"), orca, speech)
    with pytest.raises(OrcaNotConnected):
        asyncio.run(keyboard.press((parse_gesture("Tab"),), 0, 1))
    with pytest.raises(OrcaNotConnected):
        orca.write_key(KEYS["tab"], pressed=True)


@pytest.mark.skipif(sys.platform == "win32", reason="select takes no pipes on Windows")
@pytest.mark.parametrize(("printed", "line"), [(b"12\nmore\n", "12"), (b"12", "12"), (b"", "")])
def test_the_first_line_of_a_child_that_printed_or_exited(printed: bytes, line: str) -> None:
    read, write = os.pipe()
    os.write(write, printed)
    os.close(write)
    try:
        assert helper.first_line(read, "Xvfb", 5) == line
    finally:
        os.close(read)


@pytest.mark.skipif(sys.platform == "win32", reason="select takes no pipes on Windows")
def test_a_child_that_never_prints_its_first_line_fails_setup() -> None:
    read, write = os.pipe()
    try:
        with pytest.raises(helper.Failed, match="Xvfb did not start"):
            helper.first_line(read, "Xvfb", 0.2)
    finally:
        os.close(read)
        os.close(write)
