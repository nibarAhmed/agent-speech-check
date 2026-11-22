"""`asc setup`: its questions, how answers are read, and the config they make.

`prompts.py` asks the questions and prints. Everything here is pure.

Each question comes first, then its numbered choices, then a short prompt, as
in other CLI setups. The text is read by screen readers, so the default is
marked with the word "(default)" on its choice, never by a symbol alone like
`[Y/n]`, an arrow or a star: NVDA reads such a symbol aloud, or drops it at a
low punctuation level. Around a word, the brackets can go and the word stays.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, fields
from typing import Any

from agent_speech_check.config import prefs
from agent_speech_check.config.prefs import default_mode
from agent_speech_check.config.targets import ORCA_MODES, defaults


@dataclass(frozen=True)
class Choice:
    name: str
    about: str
    default: bool = False  # what Enter picks; one per question


def _choices(names: tuple[str, ...], about: dict[str, str], default: str) -> tuple[Choice, ...]:
    """Names from `prefs`, so a new mode or choice can't be missed here: a name
    without a description fails at import."""
    return tuple(Choice(name, about[name], name == default) for name in names)


def default_of(choices: tuple[Choice, ...]) -> str:
    """The name of the one default choice."""
    (name,) = (choice.name for choice in choices if choice.default)
    return name


_MODE_ABOUT = {
    "watch": "listen to your own NVDA while you use it; never sends keys",
    "desktop": "asc runs its own silent NVDA on this desktop",
    "shared": "asc presses keys in your own NVDA, in your own session",
    "remote": "NVDA or Orca on another machine, like a VM, that asc reaches over ssh",
}
_ORCA_ABOUT = {
    "watch": "listen to your own Orca while you use it; never sends keys",
    "desktop": "asc runs its own Orca on a hidden display",
}


def modes(*, named: bool, platform: str = sys.platform) -> tuple[Choice, ...]:
    """The modes that run here, as `check_runnable` says. A named instance can't join
    your own screen reader (watch and shared mode)."""
    targets = [defaults(mode, platform=platform) for mode in prefs.MODES]
    names = tuple(
        target.mode
        for target in targets
        if (target.mode in ORCA_MODES or target.reader == "nvda")
        and not (named and target.own_reader)
    )
    orca = defaults("desktop", platform=platform).reader == "orca"
    about = {**_MODE_ABOUT, **_ORCA_ABOUT} if orca else _MODE_ABOUT
    return _choices(names, about, default="remote" if named else default_mode(platform))


NVDA_SOURCES = (
    Choice("asc", "the portable NVDA asc downloads and keeps", default=True),
    Choice("folder", "a portable NVDA folder of your own"),
)
LAYOUTS = _choices(
    prefs.LAYOUTS,
    {"desktop": "NVDA's desktop keyboard layout", "laptop": "NVDA's laptop keyboard layout"},
    default="desktop",  # keys_layout's default in prefs
)
OSES = _choices(
    prefs.OSES,
    {"windows": "NVDA, already running there", "linux": "Orca, which asc starts there"},
    default="windows",
)
# Fails at import unless each question has exactly one default.
for _question in (NVDA_SOURCES, LAYOUTS, OSES):
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
    keys_layout: str = default_of(LAYOUTS)
    os: str | None = None
    ssh: str | None = None


def _every_answer(labels: dict[str, str]) -> dict[str, str]:
    """Each answer but the mode, with its name in the summary: an answer
    without a name fails at import."""
    return {field.name: labels[field.name] for field in fields(Answers) if field.name != "mode"}


LABELS = _every_answer(
    {
        "nvda_dir": "NVDA",
        "keys_layout": "Keyboard layout",
        "os": "Other machine runs",
        "ssh": "Reached with ssh",
    }
)


def _per_mode(questions: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Modes from `prefs`, so a new mode fails at import until it is listed."""
    return {mode: questions[mode] for mode in prefs.MODES}


# The answers each mode asks for, in order, where it runs (see `runs_here` and `asks`).
QUESTIONS = _per_mode(
    {
        "watch": ("keys_layout",),
        "desktop": ("nvda_dir",),
        "shared": ("keys_layout",),
        "remote": ("os", "ssh", "keys_layout"),
    }
)


def runs_here(mode: str, *, named: bool) -> bool:
    """Whether the instance runs `mode`. The default instance keeps remote only
    as the preferred mode, so setup asks nothing more for it."""
    return named or not defaults(mode).named_only


def asks(field: str, mode: str, answered: dict[str, Any], platform: str = sys.platform) -> bool:
    """Whether a question of `QUESTIONS` is asked, after the answers before it. NVDA's
    folder and layout aren't asked when Orca reads."""
    nvda_only = field in ("nvda_dir", "keys_layout")
    return not (nvda_only and defaults(mode, answered.get("os"), platform).reader == "orca")


def to_data(answers: Answers) -> dict[str, Any]:
    """The config the answers make. Defaults are left out, so the file holds
    only what was chosen."""
    table: dict[str, Any] = {}
    if answers.nvda_dir is not None:
        table["nvda_dir"] = answers.nvda_dir
    if answers.os is not None:
        table["os"] = answers.os
    if answers.ssh is not None:
        table["ssh"] = answers.ssh
    data: dict[str, Any] = {"mode": answers.mode}
    if table:
        data[answers.mode] = table
    if answers.keys_layout != default_of(LAYOUTS):
        data["defaults"] = {"keys_layout": answers.keys_layout}
    return data


def summary_lines(answers: Answers, fields: tuple[str, ...]) -> list[str]:
    """Every answer given, in the questions' words, for a last look before saving.
    Unlike `to_data`, defaults are shown too, so no answer seems lost."""
    rows = [("Mode:", answers.mode)]
    for field in fields:
        rows.append((f"{LABELS[field]}:", _shown(getattr(answers, field))))
    return ["Your settings:", *_columns(rows)]


def _shown(value: str | None) -> str:
    """An answer as the summary says it. No NVDA folder means asc's own NVDA."""
    if value is None:
        return default_of(NVDA_SOURCES)
    return value
