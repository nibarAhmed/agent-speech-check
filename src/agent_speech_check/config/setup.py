"""`asc setup`: its questions, how answers are read, and the config they make.

`cli.py` asks the questions, since only it prints. Everything here is pure.

Each question comes first, then its numbered choices, then a short prompt, as
in other CLI setups. The text is read by screen readers, so the default is
marked with the word "(default)" on its choice, never by a symbol alone like
`[Y/n]`, an arrow or a star: NVDA reads such a symbol aloud, or drops it at a
low punctuation level. Around a word, the brackets can go and the word stays.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from agent_speech_check.config import prefs


@dataclass(frozen=True)
class Choice:
    name: str
    about: str
    default: bool = False  # what Enter picks; one per question


def _choices(names: tuple[str, ...], about: dict[str, str], default: str) -> tuple[Choice, ...]:
    """Names from `prefs`, so a new mode or window can't be missed here: a name
    without a description fails at import."""
    return tuple(Choice(name, about[name], name == default) for name in names)


def default_of(choices: tuple[Choice, ...]) -> str:
    """The name of the one default choice."""
    (name,) = (choice.name for choice in choices if choice.default)
    return name


MODES = _choices(
    prefs.MODES,
    {
        "watch": "listen to your own NVDA while you use it; never sends keys",
        "desktop": "asc runs its own silent NVDA on this desktop",
        "sandbox": "asc runs its own NVDA inside Windows Sandbox",
        "shared": "asc presses keys in your own NVDA, in your own session",
    },
    default="watch",
)
NVDA_SOURCES = (
    Choice("asc", "the portable NVDA asc downloads and keeps", default=True),
    Choice("folder", "a portable NVDA folder of your own"),
)
WINDOWS = _choices(
    prefs.WINDOWS,
    {
        "tray": "hidden, with a tray icon to show it",
        "taskbar": "minimised on the taskbar",
        "show": "left open",
    },
    default="tray",  # what targets.py uses when window isn't set
)
LAYOUTS = _choices(
    prefs.LAYOUTS,
    {"desktop": "NVDA's desktop keyboard layout", "laptop": "NVDA's laptop keyboard layout"},
    default="desktop",  # keys_layout's default in prefs
)
# Fails at import unless each question has exactly one default.
for _question in (MODES, NVDA_SOURCES, WINDOWS, LAYOUTS):
    default_of(_question)


def _columns(rows: list[tuple[str, str]]) -> list[str]:
    """Two columns, indented, the first padded so the second lines up. Screen
    readers skip the spaces."""
    width = max(len(left) for left, _ in rows)
    return [f"  {left.ljust(width)}  {right}" for left, right in rows]


def choice_lines(question: str, choices: tuple[Choice, ...]) -> list[str]:
    """The question, then its numbered choices, the default marked."""
    # The colon gives a screen reader a pause between a name and what it means.
    rows = [
        (
            f"{number}  {choice.name}:",
            f"{choice.about} (default)" if choice.default else choice.about,
        )
        for number, choice in enumerate(choices, 1)
    ]
    return [question, *_columns(rows)]


def choice_prompt(choices: tuple[Choice, ...]) -> str:
    return f"Choose 1 to {len(choices)}: "


def pick(answer: str, choices: tuple[Choice, ...]) -> str | None:
    """The chosen name: Enter picks the default, then a number or a name. None if neither."""
    text = answer.strip().lower()
    if not text:
        return default_of(choices)
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
    share: tuple[str, ...] = ()
    window: str = default_of(WINDOWS)
    keys_layout: str = default_of(LAYOUTS)


def _every_answer(labels: dict[str, str]) -> dict[str, str]:
    """Each answer but the mode, with its name in the summary: an answer
    without a name fails at import."""
    return {field.name: labels[field.name] for field in fields(Answers) if field.name != "mode"}


LABELS = _every_answer(
    {
        "nvda_dir": "NVDA",
        "share": "Shared folders",
        "window": "Window",
        "keys_layout": "Keyboard layout",
    }
)


def _per_mode(questions: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Modes from `prefs`, so a new mode fails at import until it is listed."""
    return {mode: questions[mode] for mode in prefs.MODES}


# The answers each mode asks for, in order. Setup asks these, and the summary
# shows them.
QUESTIONS = _per_mode(
    {
        "watch": ("keys_layout",),
        "desktop": ("nvda_dir",),
        "sandbox": ("nvda_dir", "share", "window"),
        "shared": ("keys_layout",),
    }
)


def to_data(answers: Answers) -> dict[str, Any]:
    """The config the answers make. Defaults are left out, so the file holds
    only what was chosen."""
    table: dict[str, Any] = {}
    if answers.nvda_dir is not None:
        table["nvda_dir"] = answers.nvda_dir
    if answers.share:
        table["share"] = list(answers.share)
    if answers.window != default_of(WINDOWS):
        table["window"] = answers.window
    data: dict[str, Any] = {"mode": answers.mode}
    if table:
        data[answers.mode] = table
    if answers.keys_layout != default_of(LAYOUTS):
        data["defaults"] = {"keys_layout": answers.keys_layout}
    return data


def summary_lines(answers: Answers) -> list[str]:
    """Every answer given, in the questions' words, for a last look before saving.
    Unlike `to_data`, defaults are shown too, so no answer seems lost."""
    rows = [("Mode:", answers.mode)]
    for field in QUESTIONS[answers.mode]:
        first, *more = _shown(getattr(answers, field))
        rows.append((f"{LABELS[field]}:", first))
        rows.extend(("", value) for value in more)
    return ["Your settings:", *_columns(rows)]


def _shown(value: str | tuple[str, ...] | None) -> tuple[str, ...]:
    """An answer as the summary says it, one line per folder. No NVDA folder
    means asc's own NVDA."""
    if value is None:
        return (default_of(NVDA_SOURCES),)
    if isinstance(value, tuple):
        return value or ("none",)
    return (value,)
