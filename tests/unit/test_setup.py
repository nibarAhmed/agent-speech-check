"""`asc setup` and `asc config`, run in this process with their own state folder."""

import os
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from agent_speech_check.cli import main
from agent_speech_check.config import prefs, setup
from agent_speech_check.config.prompts import confirm_orca_restart, run_setup, say_update
from agent_speech_check.config.targets import load
from agent_speech_check.nvda.launcher import Release

FIXTURE = Path(__file__).parents[1] / "fixtures" / "keyCommands.html"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path / "agent-speech-check"


class Asker:
    """Answers setup's questions in order, and keeps the questions."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


# ---------------------------------------------------------------- the questions


def test_the_default_need_not_be_first() -> None:
    choices = (setup.Choice("a", "first"), setup.Choice("b", "second", default=True))
    assert setup.pick("", choices) == "b"
    lines = setup.choice_lines("Which?", choices)
    assert lines[1].endswith(" first")
    assert lines[2].endswith(" second (default)")


def test_summary_follows_the_questions_each_mode_asks() -> None:
    assert set(setup.QUESTIONS) == set(prefs.MODES)
    for mode, fields in setup.QUESTIONS.items():
        # The title, the mode, then one line per question.
        assert len(setup.summary_lines(setup.Answers(mode), fields)) == 2 + len(fields)


@pytest.mark.parametrize(
    ("answer", "picked"),
    [
        ("", "watch"),
        ("2", "desktop"),
        (" Shared ", "shared"),
        ("4", "remote"),
        ("5", None),
        ("x", None),
    ],
)
def test_pick(answer: str, picked: str | None) -> None:
    assert setup.pick(answer, setup.modes(named=False, platform="win32")) == picked


@pytest.mark.parametrize(
    ("answer", "result"), [("", True), ("Y", True), ("no", False), ("maybe", None)]
)
def test_yes_no(answer: str, result: bool | None) -> None:
    assert setup.yes_no(answer, default=True) is result


def test_to_data_keeps_only_choices() -> None:
    assert setup.to_data(setup.Answers("watch")) == {"mode": "watch"}
    answers = setup.Answers("desktop", "C:\\n", "laptop")
    assert setup.to_data(answers) == {
        "mode": "desktop",
        "desktop": {"nvda_dir": "C:\\n"},
        "defaults": {"keys_layout": "laptop"},
    }


# ---------------------------------------------------------------- the flow


def test_setup_watch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ask = Asker("", "laptop", "")
    assert run_setup(ask, replace_without_asking=False, platform="win32") == 0
    settings = load(root / "config.toml", platform="win32")
    assert settings.resolve(None).mode == "watch"
    assert settings.value("keys_layout")[0] == "laptop"
    out = capsys.readouterr().out
    assert out.index("Which mode should asc use?") < out.index("1  watch:")
    assert "Your settings:\n  Mode:             watch\n  Keyboard layout:  laptop\n" in out
    assert out.endswith(f"\nSettings saved: {os.path.realpath(root / 'config.toml')}\n")
    assert "[" not in out
    assert not any("[" in prompt for prompt in ask.prompts)


def test_setup_desktop_with_own_nvda(root: Path, tmp_path: Path) -> None:
    own = tmp_path / "my nvda"
    own.mkdir()
    (own / "nvda.exe").write_bytes(b"")
    ask = Asker("desktop", "2", str(tmp_path / "nope"), str(tmp_path), str(own), "y")
    run_setup(ask, replace_without_asking=False, platform="win32")
    target = load(root / "config.toml", platform="win32").resolve(None)
    assert target.nvda_dir == str(own)
    assert target.settings == ()


def test_setup_desktop_declines_the_download(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_setup(Asker("2", "", "n", "", "y"), replace_without_asking=False, platform="win32")
    out = capsys.readouterr().out
    assert "Run asc get-nvda later." in out
    assert "  NVDA:  asc\n" in out
    assert "While it runs, Control+Shift+Alt+F12 stops asc and releases keys." in out
    assert load(root / "config.toml", platform="win32").resolve(None).launches_nvda


def test_setup_shared_explains_consent(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_setup(Asker("shared", "huh", "", "y"), replace_without_asking=False, platform="win32")
    out = capsys.readouterr().out
    assert "Answer 1 to 2, or a name." in out
    # The warning comes with the choice of mode, before the next question.
    assert out.index("--i-understand at every asc start") < out.index("keyboard layout")
    assert load(root / "config.toml", platform="win32").resolve(None).mode == "shared"


def test_setup_remote_asks_where_ssh_goes(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASC_INSTANCE", "vm2")
    # Mode (an instance can't join your own NVDA, so remote is the default), Windows,
    # an ssh option refused, the destination, keyboard layout, save.
    ask = Asker("watch", "", "", "-oProxyCommand=x", "me@vm", "", "y")
    run_setup(ask, replace_without_asking=False, platform="win32")
    out = capsys.readouterr().out
    assert "Answer 1 to 2, or a name." in out
    assert "an ssh destination, like me@vm" in out
    assert "  Other machine runs:  windows\n  Reached with ssh:    me@vm\n" in out
    target = load(root / "instances" / "vm2" / "config.toml", platform="win32").resolve(None)
    assert (target.os, target.ssh, target.tunnel) == ("windows", "me@vm", True)
    # Orca reads there, so no NVDA keyboard layout is asked.
    run_setup(Asker("", "linux", "me@vm", "y"), replace_without_asking=True, platform="win32")
    assert "keyboard layout" not in capsys.readouterr().out
    assert (
        load(root / "instances" / "vm2" / "config.toml", platform="win32").resolve(None).os
        == "linux"
    )


def test_setup_remote_in_the_default_instance_keeps_only_the_mode(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_setup(Asker("remote", "y"), replace_without_asking=False, platform="win32")
    out = capsys.readouterr().out
    assert "Remote mode runs only in a named instance, one per VM:" in out
    assert "asc setup --instance VMNAME" in out
    # No port or layout: this instance won't run remote mode.
    assert "Your settings:\n  Mode:  remote\n" in out
    saved = (root / "config.toml").read_text(encoding="utf-8")
    assert 'mode = "remote"' in saved
    assert "[" not in saved


def test_setup_on_linux_offers_what_orca_runs(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Enter picks desktop mode, then save: Orca needs no NVDA folder or layout.
    ask = Asker("", "y")
    run_setup(ask, replace_without_asking=False, platform="linux")
    out = capsys.readouterr().out
    assert ask.prompts[0] == "Choose 1 to 3: "
    assert "your own Orca" in out
    assert "its own Orca on a hidden display (default)" in out
    assert "Which NVDA" not in out
    assert "keyboard layout" not in out
    assert "While it runs" not in out
    target = load(root / "config.toml", platform="linux").resolve(None)
    assert (target.mode, target.reader) == ("desktop", "orca")
    monkeypatch.setenv("ASC_INSTANCE", "vm3")
    ask = Asker("", "linux", "me@vm", "y")
    run_setup(ask, replace_without_asking=False, platform="linux")
    assert ask.prompts[0] == "Choose 1 to 2: "
    target = load(root / "instances" / "vm3" / "config.toml", platform="linux").resolve(None)
    assert (target.mode, target.os, target.ssh) == ("remote", "linux", "me@vm")


def test_setup_watch_on_linux_asks_about_restarting_orca(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Default choice (ask) is omitted from config.toml; summary still shows it.
    run_setup(Asker("watch", "", "y"), replace_without_asking=False, platform="linux")
    out = capsys.readouterr().out
    assert "should asc restart it?" in out
    assert "keyboard layout" not in out
    assert "Your settings:\n  Mode:          watch\n  Restart Orca:  ask\n" in out
    assert "[watch]" not in (root / "config.toml").read_text(encoding="utf-8")
    assert load(root / "config.toml", platform="linux").resolve(None).restart_orca == "ask"
    # Picking always saves [watch] restart_orca = "always".
    run_setup(Asker("watch", "always", "y"), replace_without_asking=True, platform="linux")
    assert load(root / "config.toml", platform="linux").resolve(None).restart_orca == "always"
    # Windows watch mode never asks about Orca.
    capsys.readouterr()
    run_setup(Asker("watch", "", "y"), replace_without_asking=True, platform="win32")
    assert "Restart Orca" not in capsys.readouterr().out


def test_confirm_orca_restart() -> None:
    assert confirm_orca_restart(Asker("")) is True
    assert confirm_orca_restart(Asker("n")) is False

    def eof(_: str) -> str:
        raise EOFError

    assert confirm_orca_restart(eof) is False


def test_setup_again_asks_before_replacing(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_setup(Asker("2", "", "n", "", "y"), replace_without_asking=False, platform="win32")
    before = (root / "config.toml").read_text(encoding="utf-8")
    capsys.readouterr()
    ask = Asker("")
    assert run_setup(ask, replace_without_asking=False, platform="win32") == 0
    assert ask.prompts[0].endswith("Continue? y or n, default n: ")
    assert capsys.readouterr().out == "asc setup\nNothing changed.\n"
    assert (root / "config.toml").read_text(encoding="utf-8") == before
    run_setup(Asker("y", "", "", "y"), replace_without_asking=False, platform="win32")
    assert load(root / "config.toml", platform="win32").resolve(None).mode == "watch"


def test_setup_replaces_a_broken_file(root: Path) -> None:
    """The way out of a file asc can't load: setup never reads it."""
    root.mkdir()
    (root / "config.toml").write_text("[watch\n", encoding="utf-8")
    assert run_setup(Asker("y", "", "", "y"), replace_without_asking=False, platform="win32") == 0
    assert load(root / "config.toml", platform="win32").resolve(None).mode == "watch"


def test_setup_not_saved(root: Path) -> None:
    run_setup(Asker("", "", "n"), replace_without_asking=True, platform="win32")
    assert not (root / "config.toml").exists()


def test_setup_needs_a_terminal(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # pytest's stdin is not a terminal, as an agent's is not.
    assert main(["setup"]) == 2
    assert "asc setup asks questions, so it needs a terminal" in capsys.readouterr().err
    assert not root.exists()


# ---------------------------------------------------------------- asc config


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_config_set_get_list_unset(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Desktop mode's port and nvda_dir are NVDA's.
    monkeypatch.setattr(sys, "platform", "win32")
    assert run(capsys, "config", "set", "settle_ms", "700") == (
        0,
        "settle_ms = 700 (global)\n",
        "",
    )
    assert run(capsys, "config", "set", "mode", "desktop")[1] == "mode = desktop (global)\n"
    assert run(capsys, "config", "set", "port", "7001")[1] == "port = 7001 (desktop mode)\n"
    assert run(capsys, "config", "set", "port", "7000", "--mode", "shared")[1] == (
        "port = 7000 (shared mode)\n"
    )
    assert run(capsys, "config", "get", "settle_ms")[1] == "700\n"
    out = run(capsys, "config", "list")[1]
    assert "mode = desktop (global)\n" in out
    assert "port = 7001 (desktop)\n" in out
    assert "settle_ms = 700 (global)\n" in out
    assert "nvda_dir = none (default)\n" in out
    assert run(capsys, "config", "get", "port", "--mode", "shared")[1] == "7000\n"
    assert run(capsys, "config", "unset", "settle_ms")[1] == "settle_ms unset (global)\n"
    assert run(capsys, "config", "get", "settle_ms")[1] == "400\n"
    assert run(capsys, "config", "path")[1] == f"{root / 'config.toml'}\n"
    assert load(root / "config.toml").resolve("shared").port == 7000
    # As the hints write it: --instance after the action, too.
    monkeypatch.delenv("ASC_INSTANCE", raising=False)
    assert (
        run(capsys, "config", "set", "ssh", "me@vm", "--mode", "remote", "--instance", "vm2")[0]
        == 0
    )
    written = tomllib.loads((root / "instances" / "vm2" / "config.toml").read_text("utf-8"))
    assert written["remote"]["ssh"] == "me@vm"


def test_config_errors(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "config", "set", "prot", "1")
    assert code == 2
    assert "hint: did you mean port?" in err
    code, _, err = run(capsys, "config", "set", "settle_ms", "1", "--mode", "watch")
    assert code == 2
    assert "is global, so it takes no --mode" in err
    code, _, err = run(capsys, "config", "set", "keys", "true")
    assert code == 2
    assert 'unknown setting "keys"' in err
    code, _, err = run(capsys, "config", "set", "nvda_dir", "C:\\nvda", "--mode", "watch")
    assert code == 2
    assert "[watch] nvda_dir: expected only for an NVDA that asc launches" in err
    code, _, err = run(capsys, "config", "set", "os", "linux", "--mode", "desktop")
    assert code == 2
    assert "os: expected only in remote mode" in err
    assert not (root / "config.toml").exists()


@pytest.mark.usefixtures("root")
def test_settings_fill_in_left_out_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    folder = tmp_path / "nvda" / "documentation" / "en"
    folder.mkdir(parents=True)
    shutil.copy(FIXTURE, folder / "keyCommands.html")
    run(capsys, "config", "set", "mode", "desktop")
    run(capsys, "config", "set", "nvda_dir", str(tmp_path / "nvda"))
    search = ["keys", "sleep", "mode", "toggle"]
    assert run(capsys, *search)[1].startswith("NVDA+shift+s  ")
    run(capsys, "config", "set", "keys_layout", "laptop")
    assert run(capsys, *search)[1].startswith("NVDA+shift+z  ")
    # A flag beats the setting.
    assert run(capsys, *search, "--layout", "desktop")[1].startswith("NVDA+shift+s  ")


def test_config_path_works_with_a_broken_file(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root.mkdir()
    (root / "config.toml").write_text("[watch\n", encoding="utf-8")
    assert run(capsys, "config", "path") == (0, f"{root / 'config.toml'}\n", "")


@pytest.mark.usefixtures("root")
def test_mode_is_checked_before_looking_for_a_daemon(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A corrupt state file is deleted when a daemon is looked for there.
    outside = tmp_path / "state.json"
    outside.write_text("{half", encoding="utf-8")
    code, _, err = run(capsys, "config", "set", "port", "1", "--mode", "..")
    assert code == 2
    assert "invalid choice: '..'" in err
    assert outside.exists()


@pytest.mark.usefixtures("root")
def test_keys_refuses_a_bad_language(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "keys", "--lang", "../x", "heading")
    assert code == 2
    assert "--lang: expected a language code like en or pt_BR" in err


# ---------------------------------------------------------------- a newer NVDA at start

NEWER = Release("2027.1", "a5b01592f4e2355432848d3ad82f5bd214f25947")


def test_without_a_terminal_a_newer_nvda_is_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    # Agents have no terminal: start goes on, and get-nvda refuses while its NVDA runs.
    say_update(NEWER, None, None)
    assert capsys.readouterr().out == "NVDA 2027.1 is available: asc get-nvda when asc is stopped\n"


def test_an_nvda_dir_is_never_updated(capsys: pytest.CaptureFixture[str]) -> None:
    ask = Asker()
    say_update(NEWER, "C:/my-nvda", ask)
    assert capsys.readouterr().out == "NVDA 2027.1 is available (nvda_dir is older)\n"
    assert ask.prompts == []


def test_at_a_terminal_the_update_is_offered(capsys: pytest.CaptureFixture[str]) -> None:
    ask = Asker("n")
    say_update(NEWER, None, ask)
    assert "NVDA 2027.1 is available. Update before starting?" in ask.prompts[0]
    assert "Run asc get-nvda when asc is stopped." in capsys.readouterr().out


def test_no_answer_to_the_update_is_a_no(capsys: pytest.CaptureFixture[str]) -> None:
    # Ctrl+Z, Enter at the prompt: start goes on with the NVDA it has.
    def eof(_: str) -> str:
        raise EOFError

    say_update(NEWER, None, eof)
    assert "Run asc get-nvda when asc is stopped." in capsys.readouterr().out
