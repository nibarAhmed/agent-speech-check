"""`asc setup`: its questions, how answers are read, and the config they make.

`cli.py` asks the questions, since only it prints. Everything here is pure.

Each question comes first, then its numbered choices, then a short prompt, as
in other CLI setups. The text is read by screen readers, so the default is
marked with the word "(default)" on its choice, never by a symbol alone like
`[Y/n]`, an arrow or a star: NVDA reads such a symbol aloud, or drops it at a
low punctuation level. Around a word, the brackets can go and the word stays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_speech_check.config import prefs
from agent_speech_check.config.edit import show
from agent_speech_check.config.targets import DEFAULT_TARGET


@dataclass(frozen=True)
class Choice:
    name: str
    about: str


def _choices(names: tuple[str, ...], about: dict[str, str]) -> tuple[Choice, ...]:
    """Names from `prefs`, so a new mode or window can't be missed here: a name
    without a description fails at import."""
    return tuple(Choice(name, about[name]) for name in names)


MODES = _choices(
    prefs.MODES,
    {
        "watch": "listen to your own NVDA while you use it; never sends keys",
        "desktop": "asc runs its own silent NVDA on this desktop",
        "sandbox": "asc runs its own NVDA inside Windows Sandbox",
        "shared": "asc presses keys in your own NVDA, in your own session",
    },
)
NVDA_SOURCES = (
    Choice("asc", "the portable NVDA asc downloads and keeps"),
    Choice("folder", "a portable NVDA folder of your own"),
)
PUNCTUATION = (
    Choice("keep", "what that NVDA's own config says"),
    Choice("none", "no punctuation"),
    Choice("some", "some punctuation"),
    Choice("most", "most punctuation"),
    Choice("all", "all punctuation"),
)
# NVDA's speech.symbolLevel values for each punctuation level.
SYMBOL_LEVELS = {"none": "0", "some": "100", "most": "200", "all": "300"}
WINDOWS = _choices(
    prefs.WINDOWS,
    {
        "tray": "hidden, with a tray icon to show it",
        "taskbar": "minimised on the taskbar",
        "show": "left open",
    },
)
LAYOUTS = _choices(
    prefs.LAYOUTS,
    {"desktop": "NVDA's desktop keyboard layout", "laptop": "NVDA's laptop keyboard layout"},
)


def _columns(rows: list[tuple[str, str]]) -> list[str]:
    """Two columns, indented, the first padded so the second lines up. Screen
    readers skip the spaces."""
    width = max(len(left) for left, _ in rows)
    return [f"  {left.ljust(width)}  {right}" for left, right in rows]


def choice_lines(question: str, choices: tuple[Choice, ...]) -> list[str]:
    """The question, then its numbered choices, the first marked as the default."""
    # The colon gives a screen reader a pause between a name and what it means.
    rows = [(f"{number}  {choice.name}:", choice.about) for number, choice in enumerate(choices, 1)]
    rows[0] = (rows[0][0], f"{rows[0][1]} (default)")
    return [question, *_columns(rows)]


def choice_prompt(choices: tuple[Choice, ...]) -> str:
    return f"Choose 1 to {len(choices)}: "


def pick(answer: str, choices: tuple[Choice, ...]) -> str | None:
    """The chosen name: Enter picks the first, then a number or a name. None if neither."""
    text = answer.strip().lower()
    if not text:
        return choices[0].name
    if text.isdigit() and 1 <= int(text) <= len(choices):
        return choices[int(text) - 1].name
    names = [choice.name for choice in choices]
    return text if text in names else None


def yes_no_prompt(question: str, *, default: bool) -> str:
    return f"{question} y or n, default {'y' if default else 'n'}: "


def yes_no(answer: str, *, default: bool) -> bool | None:
    text = answer.strip().lower()
    if not text:
        return default
    if text in ("y", "yes"):
        return True
    if text in ("n", "no"):
        return False
    return None


@dataclass(frozen=True)
class Answers:
    mode: str
    nvda_dir: str | None = None
    punctuation: str = "keep"
    share: tuple[str, ...] = ()
    window: str = "tray"
    keys_layout: str = "desktop"


def to_data(answers: Answers) -> dict[str, Any]:
    """The config the answers make. Defaults are left out, so the file holds
    only what was chosen."""
    table: dict[str, Any] = {"mode": answers.mode}
    if answers.nvda_dir is not None:
        table["nvda_dir"] = answers.nvda_dir
    if answers.share:
        table["share"] = list(answers.share)
    if answers.window != WINDOWS[0].name:
        table["window"] = answers.window
    if answers.punctuation in SYMBOL_LEVELS:
        table["nvda_settings"] = [f"speech.symbolLevel={SYMBOL_LEVELS[answers.punctuation]}"]
    data: dict[str, Any] = {"targets": {DEFAULT_TARGET: table}}
    if answers.keys_layout != LAYOUTS[0].name:
        data["defaults"] = {"keys_layout": answers.keys_layout}
    return data


def settings_lines(data: dict[str, Any]) -> list[str]:
    """What `to_data` made, one setting a line, for a last look before saving."""
    chosen = {**data["targets"][DEFAULT_TARGET], **data.get("defaults", {})}
    return ["Your settings:", *_columns([(name, show(value)) for name, value in chosen.items()])]
