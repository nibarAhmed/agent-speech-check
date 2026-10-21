"""`asc setup` and `asc config`, run in this process with their own state folder."""

import os
import shutil
from pathlib import Path

import pytest

from agent_speech_check.cli import main, run_setup, say_update
from agent_speech_check.config import prefs, setup
from agent_speech_check.config.targets import load
from agent_speech_check.errors import AscError
from agent_speech_check.nvda.launcher import Release
from agent_speech_check.sandbox import wsb

FIXTURE = Path(__file__).parents[1] / "fixtures" / "keyCommands.html"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
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


def test_prompts_are_plain_words() -> None:
    prompt = setup.choice_prompt(setup.MODES)
    assert prompt == "Choose 1 to 4: "
    assert setup.yes_no_prompt("Save?", default=False) == "Save? y or n, default n: "
    assert "[" not in prompt


def test_choice_lines_put_the_question_first() -> None:
    lines = setup.choice_lines("Which NVDA?", setup.NVDA_SOURCES)
    assert lines == [
        "Which NVDA?",
        "  1  asc:     the portable NVDA asc downloads and keeps (default)",
        "  2  folder:  a portable NVDA folder of your own",
    ]


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
        assert len(setup.summary_lines(setup.Answers(mode))) == 2 + len(fields)


def test_summary_shows_every_answer_for_the_mode() -> None:
    answers = setup.Answers("sandbox", None, ("C:\\a", "C:\\b"), "show")
    assert setup.summary_lines(answers) == [
        "Your settings:",
        "  Mode:            sandbox",
        "  NVDA:            asc",
        "  Shared folders:  C:\\a",
        "                   C:\\b",
        "  Window:          show",
    ]


def test_summary_says_none_and_your_folder() -> None:
    assert setup.summary_lines(setup.Answers("watch")) == [
        "Your settings:",
        "  Mode:             watch",
        "  Keyboard layout:  desktop",
    ]
    assert setup.summary_lines(setup.Answers("desktop", "C:\\n"))[2] == "  NVDA:  C:\\n"
    assert setup.summary_lines(setup.Answers("sandbox"))[3] == "  Shared folders:  none"


@pytest.mark.parametrize(
    ("answer", "picked"),
    [("", "watch"), ("2", "desktop"), (" Sandbox ", "sandbox"), ("5", None), ("x", None)],
)
def test_pick(answer: str, picked: str | None) -> None:
    assert setup.pick(answer, setup.MODES) == picked


@pytest.mark.parametrize(
    ("answer", "result"), [("", True), ("Y", True), ("no", False), ("maybe", None)]
)
def test_yes_no(answer: str, result: bool | None) -> None:
    assert setup.yes_no(answer, default=True) is result


def test_to_data_keeps_only_choices() -> None:
    assert setup.to_data(setup.Answers("watch")) == {"mode": "watch"}
    answers = setup.Answers("sandbox", "C:\\n", ("C:\\a",), "show", "laptop")
    assert setup.to_data(answers) == {
        "mode": "sandbox",
        "sandbox": {"nvda_dir": "C:\\n", "share": ["C:\\a"], "window": "show"},
        "defaults": {"keys_layout": "laptop"},
    }


# ---------------------------------------------------------------- the flow


def test_setup_watch(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ask = Asker("", "laptop", "")
    assert run_setup(ask, replace_without_asking=False) == 0
    settings = load(root / "config.toml")
    assert settings.resolve(None).mode == "watch"
    assert settings.value("keys_layout")[0] == "laptop"
    out = capsys.readouterr().out
    assert out.index("How should asc work with NVDA?") < out.index("1  watch:")
    assert "Your settings:\n  Mode:             watch\n  Keyboard layout:  laptop\n" in out
    assert out.endswith(f"\nSettings saved: {os.path.realpath(root / 'config.toml')}\n")
    assert "[" not in out
    assert not any("[" in prompt for prompt in ask.prompts)


def test_setup_desktop_with_own_nvda(root: Path, tmp_path: Path) -> None:
    own = tmp_path / "my nvda"
    own.mkdir()
    (own / "nvda.exe").write_bytes(b"")
    ask = Asker("desktop", "2", str(tmp_path / "nope"), str(tmp_path), str(own), "y")
    run_setup(ask, replace_without_asking=False)
    target = load(root / "config.toml").resolve(None)
    assert target.nvda_dir == str(own)
    assert target.settings == ()


def test_setup_desktop_declines_the_download(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_setup(Asker("2", "", "n", "", "y"), replace_without_asking=False)
    out = capsys.readouterr().out
    assert "Run asc get-nvda later." in out
    assert "While it runs, Control+Shift+Alt+F12 stops asc and releases keys." in out
    assert load(root / "config.toml").resolve(None).launches_portable


def test_setup_shared_explains_consent(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_setup(Asker("shared", "huh", "", "y"), replace_without_asking=False)
    out = capsys.readouterr().out
    assert "Answer 1 to 2, or a name." in out
    # The warning comes with the choice of mode, before the next question.
    assert out.index("--i-understand at every asc start") < out.index("keyboard layout")
    assert load(root / "config.toml").resolve(None).mode == "shared"


def test_setup_without_sandbox_says_how_to_get_it(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(AscError) as unavailable:
        wsb.require_available()
    run_setup(Asker("sandbox", "", "", "y"), replace_without_asking=False)
    out = capsys.readouterr().out
    assert f"({unavailable.value.hint}). Pick another mode." in out
    assert load(root / "config.toml").resolve(None).mode == "watch"


def test_setup_again_asks_before_replacing(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_setup(Asker("2", "", "n", "", "y"), replace_without_asking=False)
    before = (root / "config.toml").read_text(encoding="utf-8")
    capsys.readouterr()
    ask = Asker("")
    assert run_setup(ask, replace_without_asking=False) == 0
    assert ask.prompts[0].endswith("Continue? y or n, default n: ")
    assert capsys.readouterr().out == "asc setup\nNothing changed.\n"
    assert (root / "config.toml").read_text(encoding="utf-8") == before
    run_setup(Asker("y", "", "", "y"), replace_without_asking=False)
    assert load(root / "config.toml").resolve(None).mode == "watch"


def test_setup_replaces_a_broken_file(root: Path) -> None:
    """The way out of a file asc can't load: setup never reads it."""
    root.mkdir()
    (root / "config.toml").write_text("[watch\n", encoding="utf-8")
    assert run_setup(Asker("y", "", "", "y"), replace_without_asking=False) == 0
    assert load(root / "config.toml").resolve(None).mode == "watch"


def test_setup_not_saved(root: Path) -> None:
    run_setup(Asker("", "", "n"), replace_without_asking=True)
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


def test_config_set_get_list_unset(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "config", "set", "settle_ms", "700") == (
        0,
        "settle_ms = 700 (global)\n",
        "",
    )
    assert run(capsys, "config", "set", "mode", "desktop")[1] == "mode = desktop (global)\n"
    assert run(capsys, "config", "set", "launch", "false")[1] == "launch = false (desktop mode)\n"
    assert run(capsys, "config", "set", "port", "7000", "--mode", "sandbox")[1] == (
        "port = 7000 (sandbox mode)\n"
    )
    assert run(capsys, "config", "get", "settle_ms")[1] == "700\n"
    out = run(capsys, "config", "list")[1]
    assert "mode = desktop (global)\n" in out
    assert "launch = false (desktop)\n" in out
    assert "settle_ms = 700 (global)\n" in out
    assert "nvda_dir = none (default)\n" in out
    assert run(capsys, "config", "get", "port", "--mode", "sandbox")[1] == "7000\n"
    assert run(capsys, "config", "get", "launch", "--mode", "sandbox")[1] == "true\n"
    assert run(capsys, "config", "unset", "settle_ms")[1] == "settle_ms unset (global)\n"
    assert run(capsys, "config", "get", "settle_ms")[1] == "400\n"
    assert run(capsys, "config", "path")[1] == f"{root / 'config.toml'}\n"
    assert load(root / "config.toml").resolve("sandbox").port == 7000


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
    # No mode set means watch mode, which has no sandbox window.
    code, _, err = run(capsys, "config", "set", "window", "show")
    assert code == 2
    assert "window: expected only in sandbox mode" in err
    assert not (root / "config.toml").exists()


@pytest.mark.usefixtures("root")
def test_settings_fill_in_left_out_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_remove_target_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "config", "remove-target", "app")
    assert code == 2
    assert "invalid choice: 'remove-target'" in err


@pytest.mark.usefixtures("root")
@pytest.mark.parametrize("name", ["keys_lang", "keys_braille", "nvda_version"])
def test_removed_settings_are_unknown(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "config", "set", name, "x")
    assert code == 2
    assert f'unknown setting "{name}"' in err


@pytest.mark.usefixtures("root")
def test_keys_refuses_a_bad_language(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "keys", "--lang", "../x", "heading")
    assert code == 2
    assert "--lang: expected a language code like en or pt_BR" in err


def test_clear_is_gone(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "clear")
    assert code == 2
    assert "invalid choice: 'clear'" in err


def test_keys_flags_default_without_settings(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["keys", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "falling back to en (default: en)" in out
    assert "include braille displays (default: off)" in out


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
