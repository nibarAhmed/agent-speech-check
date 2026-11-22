import argparse
import os
import re
import shutil
import sys
from pathlib import Path

import configobj
import pytest

from agent_speech_check import __version__
from agent_speech_check.cli import COMMANDS, _show_status, build_parser, main, nvda_ini, run
from agent_speech_check.config import prefs
from agent_speech_check.errors import OperationalError, UsageError

ROOT = Path(__file__).parents[2]
CLI_DOC = ROOT / "docs" / "cli.md"
SKILL = ROOT / "plugins" / "agent-speech-check" / "skills" / "asc" / "SKILL.md"
DOCS = (CLI_DOC, SKILL)


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    actions = parser._actions
    return next((a.choices for a in actions if isinstance(a, argparse._SubParsersAction)), {})


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    own = {o for a in parser._actions for o in a.option_strings if o.startswith("--")}
    return own.union(*(_flags(sub) for sub in _subparsers(parser).values()))


def _mentioned_flags(text: str) -> set[str]:
    return set(re.findall(r"--[a-z]+(?:-[a-z]+)*", text))


def test_cli_doc_matches_the_parser() -> None:
    parser = build_parser()
    parts = re.split(r"^### `asc ([a-z-]+)`$", CLI_DOC.read_text(encoding="utf-8"), flags=re.M)
    names = parts[1::2]
    assert sorted(names) == sorted(COMMANDS)
    sections = {name: body.split("\n## ")[0] for name, body in zip(names, parts[2::2], strict=True)}
    for name, sub in _subparsers(parser).items():
        missing = _flags(sub) - {"--help", "--instance"} - _mentioned_flags(sections[name])
        assert not missing, f"docs/cli.md, asc {name}: {missing}"
    every_flag = _flags(parser)
    for doc in DOCS:
        unknown = _mentioned_flags(doc.read_text(encoding="utf-8")) - every_flag
        assert not unknown, f"{doc.name}: {unknown}"


def test_docs_name_only_real_settings() -> None:
    text = CLI_DOC.read_text(encoding="utf-8")
    tables = text[text.index("\n## Configuration") : text.index("\n## Command reference")]
    assert sorted(re.findall(r"^\| `([a-z_]+)` \|", tables, re.M)) == sorted(prefs.BY_NAME)
    for doc in DOCS:
        named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", doc.read_text(encoding="utf-8")))
        assert named <= set(prefs.BY_NAME), f"{doc.name}: {named - set(prefs.BY_NAME)}"


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version(capsys: pytest.CaptureFixture[str], flag: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"asc {__version__}"


def test_help_layout_and_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "positional arguments:" not in out
    assert out.index("options:") < out.index("commands:") < out.index("examples:")
    assert "/docs/cli.md#exit-codes" in out

    with pytest.raises(SystemExit) as exc:
        main(["press", "--help"])
    assert exc.value.code == 0
    press_out = capsys.readouterr().out
    assert press_out.index("positional arguments:") < press_out.index("options:")
    assert press_out.index("options:") < press_out.index("examples:")
    assert "key gesture(s)" in press_out


def test_no_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: no command given\nhint: asc --help\n"


def test_bad_arguments_are_a_short_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    # Like every other error: exit 2, one error line and a hint, no usage block.
    assert main(["press", "Tab", "--no-such-flag"]) == 2
    err = capsys.readouterr().err
    assert err == "error: unrecognized arguments: --no-such-flag\nhint: asc press --help\n"


@pytest.mark.parametrize("argv", [["speech"], ["wait", "OK"]])
def test_reading_speech_needs_since(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert (
        err == f"error: the following arguments are required: --since\nhint: asc {argv[0]} --help\n"
    )


@pytest.mark.parametrize(
    ("argv", "hint"),
    [
        (["config", "path", "--no-such-flag"], "asc config path --help"),
        (["--no-such-flag", "status"], "asc --help"),
    ],
)
def test_bad_arguments_hint_names_the_parser_that_refused_them(
    capsys: pytest.CaptureFixture[str], argv: list[str], hint: str
) -> None:
    assert main(argv) == 2
    assert capsys.readouterr().err.endswith(f"\nhint: {hint}\n")


def test_bad_config_action_arguments_are_a_short_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["config", "get"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: the following arguments are required: NAME\n")
    assert err.endswith("hint: asc config get --help\n")
    assert main(["config", "--mode", "watch", "path"]) == 2
    assert capsys.readouterr().err.endswith("hint: asc config path --help\n")


def test_config_without_action_defaults_to_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("ASC_INSTANCE", raising=False)
    assert main(["config"]) == 0
    bare = capsys.readouterr().out
    assert main(["config", "list"]) == 0
    assert bare == capsys.readouterr().out
    assert main(["config", "--mode", "desktop"]) == 0
    bare_mode = capsys.readouterr().out
    assert main(["config", "list", "--mode", "desktop"]) == 0
    assert bare_mode == capsys.readouterr().out


def test_status_all_lists_configured_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("ASC_INSTANCE", raising=False)
    base = tmp_path / "agent-speech-check"
    base.mkdir()
    (base / "config.toml").write_text('mode = "remote"\n', encoding="utf-8")
    vm1 = base / "instances" / "vm1"
    vm1.mkdir(parents=True)
    # Only a [remote] table, as `asc config set ... --mode remote --instance vm1` writes.
    (vm1 / "config.toml").write_text('[remote]\nos = "windows"\nssh = "me@vm"\n', encoding="utf-8")
    box = base / "instances" / "linux-box"
    box.mkdir(parents=True)
    (box / "config.toml").write_text(
        'mode = "remote"\n[remote]\nos = "linux"\nssh = "me@box"\n', encoding="utf-8"
    )
    broken = base / "instances" / "broken"
    broken.mkdir(parents=True)
    (broken / "config.toml").write_text("not = [valid\n", encoding="utf-8")

    assert main(["status", "--all"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "default: remote, not running"
    assert lines[1].startswith("broken: bad config (")
    assert lines[2] == "linux-box: remote (linux, me@box), not running"
    assert lines[3] == "vm1: remote (windows, me@vm), not running"

    assert main(["status", "--all", "--nvda-ini"]) == 2
    assert capsys.readouterr().err.endswith("hint: asc status --help\n")


def test_unexpected_exception_is_internal_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASC_DEBUG", raising=False)
    # A non-string argument makes argparse itself raise TypeError: a genuine
    # unexpected exception, without mocking any of our code.
    assert main([123]) == 4  # type: ignore[list-item]
    err = capsys.readouterr().err
    assert err.startswith("internal error: TypeError: ")
    assert "logs: " in err
    assert "Traceback" not in err


def test_debug_shows_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASC_DEBUG", "1")
    assert main([123]) == 4  # type: ignore[list-item]
    assert "Traceback" in capsys.readouterr().err


class _PressesCtrlC:
    """An argv item that raises KeyboardInterrupt as soon as argparse looks at it."""

    def _interrupt(self, *_: object) -> bool:
        raise KeyboardInterrupt

    __eq__ = _interrupt
    __getitem__ = _interrupt
    __hash__ = None  # type: ignore[assignment]


def test_ctrl_c_exits_130() -> None:
    assert main([_PressesCtrlC()]) == 130  # type: ignore[list-item]


class _FailsInATaskGroup:
    """An argv item that raises what a failed asyncio.TaskGroup raises."""

    def _fail(self, *_: object) -> bool:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("tray icon")])

    __eq__ = _fail
    __getitem__ = _fail
    __hash__ = None  # type: ignore[assignment]


def test_internal_error_in_a_group_names_the_inner_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The daemon's err file is all a user sees, so the real cause must be in it.
    monkeypatch.delenv("ASC_DEBUG", raising=False)
    assert main([_FailsInATaskGroup()]) == 4  # type: ignore[list-item]
    assert "internal error: ValueError: tray icon\n" in capsys.readouterr().err


def test_nvda_ini_connects_the_remote_nvda_unattended() -> None:
    status = {
        "mode": "remote",
        "reader": "nvda",
        "host": "pc",
        "port": 26837,
        "channel": "key",
        "fingerprint": "f",
    }
    remote = configobj.ConfigObj(nvda_ini(status).splitlines())["remote"]
    server = remote["controlServer"]
    assert (server["host"], server["port"], server["key"]) == ("pc:26837", "26837", "key")
    assert remote["trustedCertificates"]["pc:26837"] == "f"
    with pytest.raises(OperationalError, match="remote mode only"):
        nvda_ini({**status, "mode": "watch"})
    with pytest.raises(OperationalError, match="Orca"):
        nvda_ini({**status, "port": None, "reader": "orca"})


def test_status_lines(capsys: pytest.CaptureFixture[str]) -> None:
    status = {
        "mode": "remote",
        "keys": True,
        "reader": "nvda",
        "ssh": "me@vm",
        "tunnel": {"up": False, "error": "ssh me@vm failed: Host key verification failed."},
        "connected": True,
        "port": 6837,
        "browse_focus": "focus",
        "lines": 0,
        "latest": 0,
        "last_error": None,
    }
    _show_status(status)
    said = "over ssh: me@vm, tunnel down (ssh me@vm failed: Host key verification failed.)\n"
    assert said in capsys.readouterr().out
    _show_status({**status, "reader": "orca", "browse_focus": "browse"})
    assert "browse/focus: browse\n" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["2026.2&calc", "latest", "2026,2", ""])
def test_get_nvda_refuses_a_bad_version_before_anything(
    version: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The version goes into the download URL and a file name.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert main(["get-nvda", "--version", version]) == 2
    assert "--version" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())


def test_printed_paths_are_real(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store Python redirects %LOCALAPPDATA% for itself only, so a printed path
    must be the real one, or other programs won't find it. Without Store
    Python the paths are already real and this passes trivially."""
    root = Path(os.environ["LOCALAPPDATA"]) / f"asc-test-{os.getpid()}"
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    monkeypatch.delenv("ASC_DEBUG", raising=False)
    try:
        (root / "agent-speech-check").mkdir(parents=True)
        assert main(["config", "path"]) == 0
        printed = capsys.readouterr().out.strip()
        assert printed == os.path.realpath(printed)
        assert main([123]) == 4  # type: ignore[list-item]
        logs = capsys.readouterr().err.split("logs: ")[1].strip()
        assert logs == os.path.realpath(logs)
    finally:
        shutil.rmtree(os.path.realpath(root), ignore_errors=True)


def test_windows_only_commands_on_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert COMMANDS["get-nvda"].windows_only
    with pytest.raises(UsageError, match="needs Windows"):
        run(["get-nvda"])


@pytest.mark.parametrize(
    ("os_line", "error", "match"),
    [
        ('os = "windows"', OperationalError, "cannot run ssh"),
        ('os = "linux"', OperationalError, "cannot run ssh"),
        ("", UsageError, "remote mode needs os"),
    ],
)
def test_keys_in_remote_mode_reads_the_other_machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    os_line: str,
    error: type[Exception],
    match: str,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    config = tmp_path / "agent-speech-check" / "config.toml"
    config.parent.mkdir()
    config.write_text(f'[remote]\n{os_line}\nssh = "me@box"\n', encoding="utf-8")
    with pytest.raises(error, match=match):
        run(["keys", "--mode", "remote"])


def test_keys_reads_orcas_help_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))
    page = tmp_path / "help" / "C" / "orca" / "commands_a.page"
    page.parent.mkdir(parents=True)
    keys = "<keyseq><key>Orca Modifier</key><key>T</key></keyseq>"
    item = f"<item><p>Present the title: {keys}</p></item>"
    page.write_text(
        '<page xmlns="http://projectmallard.org/1.0/" id="a"><title>A</title>'
        f"<list>{item}{item}</list></page>",  # Orca's help repeats some commands
        encoding="utf-8",
    )
    assert run(["keys", "--mode", "desktop", "title"]) == 0
    assert capsys.readouterr().out == "Orca+T  Present the title\n"
    with pytest.raises(UsageError, match="shared mode needs Windows"):
        run(["keys", "--mode", "shared", "title"])
