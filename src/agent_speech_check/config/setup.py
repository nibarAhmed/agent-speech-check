"""`asc setup`: its questions, how answers are read, and the config they make.

`cli.py` asks the questions, since only it prints. Everything here is pure.

The questions are read by screen readers, so each is one plain line, and the
default is said in words ("default watch"), never marked with brackets: NVDA
reads a bracket aloud, or drops it at a low punctuation level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_speech_check.config import prefs
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


def choice_prompt(label: str, choices: tuple[Choice, ...]) -> str:
    return f"{label}, 1 to {len(choices)} or a name, default {choices[0].name}: "


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
