"""The questions `asc setup` asks at a terminal, the NVDA update offer, and the
download action it shares with `asc get-nvda`.

What they ask and how answers are read is in `setup.py`; this module asks and
says what happened. Only it and `cli.py` print.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from agent_speech_check.config import edit, prefs, setup
from agent_speech_check.config.targets import defaults
from agent_speech_check.daemon import session
from agent_speech_check.errors import AlreadyRunning, AscError, UsageError
from agent_speech_check.inputs import keys
from agent_speech_check.nvda import launcher
from agent_speech_check.system import paths
from agent_speech_check.update import uninstall

type Ask = Callable[[str], str]


def make_portable(
    version: str | None = None, installer: Path | None = None, sha1: str | None = None
) -> None:
    """asc's own portable NVDA, from `installer`, else a download of `version`
    (checked against `sha1` if given), else of the latest NVDA."""
    launcher.refuse_if_running()
    if installer is None:
        installer = _download(version, sha1)
    exe = launcher.create_portable(installer, launcher.managed_dir())
    print(f"portable NVDA ready: {exe} (from {installer.name})")


def _download(version: str | None, sha1: str | None) -> Path:
    if version is None:
        print("downloading the latest NVDA")
        return launcher.latest_installer(print)
    print(f"downloading {launcher.installer_url(version)}")
    return launcher.download(version, sha1)


def say_update(release: launcher.Release, nvda_dir: str | None, ask: Ask | None) -> None:
    """At a terminal, offer to update asc's own NVDA. Without one, as when an
    agent runs asc, a line says how. An NVDA the user named is never updated."""
    news = f"NVDA {release.version} is available"
    # get-nvda refuses while asc's NVDA runs, and start may have failed by then.
    when = "when asc is stopped"
    if nvda_dir is not None:
        print(f"{news} (nvda_dir is older)")
    elif ask is None:
        print(f"{news}: asc get-nvda {when}")
    else:
        # The release already looked up, so a second lookup can't pick another.
        update = partial(make_portable, release.version, sha1=release.sha1)
        try:
            _offer(ask, f"{news}. Update before starting?", update, "asc get-nvda", when)
        except EOFError:  # no answer: start with the NVDA it has
            print(f"\nRun asc get-nvda {when}.")


def _section(*lines: str) -> None:
    """A blank line, then the lines: one step of setup."""
    print("", *lines, sep="\n")


def _ask_choice(ask: Ask, question: str, choices: tuple[setup.Choice, ...]) -> str:
    _section(*setup.choice_lines(question, choices))
    while True:
        picked = setup.pick(ask(setup.choice_prompt(choices)), choices)
        if picked is not None:
            return picked
        print(f"Answer 1 to {len(choices)}, or a name.")


def _ask_yes(ask: Ask, question: str, *, default: bool) -> bool:
    while True:
        answer = setup.yes_no(ask(setup.yes_no_prompt(question, default=default)), default=default)
        if answer is not None:
            return answer
        print("Answer y or n.")


def _ask_folder(ask: Ask, prompt: str, *, holding: str | None = None) -> str | None:
    """A full path to an existing folder (with the file `holding` in it), or None on Enter."""
    while True:
        text = ask(prompt).strip().strip('"')
        if not text:
            return None
        folder = Path(text).absolute()
        if not folder.is_dir():
            print(f"No such folder: {folder}")
        elif holding and not (folder / holding).is_file():
            print(f"No {holding} in {folder}")
        else:
            return str(folder)


def _sentence(error: AscError) -> str:
    """The error as setup says it, with its hint, since setup goes on after it."""
    text = f"{error.message} ({error.hint})" if error.hint else error.message
    return f"{text[:1].upper()}{text[1:]}."


def _offer(
    ask: Ask, question: str, action: Callable[[], None], later: str, when: str = "later"
) -> None:
    """Do `action` now if the user agrees. Otherwise, or if it fails, name the
    command `later` that does it, and `when`."""
    if _ask_yes(ask, question, default=True):
        try:
            action()
        except AscError as error:
            print(f"Not done. {_sentence(error)}")
        else:
            return
    print(f"Run {later} {when}.")


def _get_nvda_now(ask: Ask) -> None:
    if (launcher.managed_dir() / "nvda.exe").is_file():
        return
    print()
    question = "asc has no portable NVDA yet. Download it now?"
    _offer(ask, question, make_portable, "asc get-nvda")


def _ask_mode(ask: Ask, *, named: bool, platform: str) -> str:
    modes = setup.modes(named=named, platform=platform)
    mode = _ask_choice(ask, "Which mode should asc use?", modes)
    target = defaults(mode, platform=platform)
    if target.launches_nvda and launcher.running():
        print("Note: desktop mode won't start while another NVDA runs.")
    if mode == "shared":
        print("Note: shared mode needs --i-understand at every asc start.")
    if not setup.runs_here(mode, named=named):
        print("Remote mode runs only in a named instance, one per VM:")
        print("asc setup --instance VMNAME. Agents make their own.")
    elif mode == "remote":
        print("Note: asc reaches it with ssh and a key, never a password.")
    if target.takes_panic_key:
        print(f"While it runs, {keys.PANIC_KEY} stops asc and releases keys.")
    return mode


def _ask_nvda_dir(ask: Ask, _mode: str) -> str | None:
    nvda_dir = None
    if _ask_choice(ask, "Which NVDA should asc run?", setup.NVDA_SOURCES) == "folder":
        nvda_dir = _ask_folder(ask, "Your portable NVDA folder: ", holding="nvda.exe")
    if nvda_dir is None:
        _get_nvda_now(ask)
    return nvda_dir


def _ask_ssh(ask: Ask, _mode: str) -> str:
    """Where ssh connects, checked as the file checks it."""
    while True:
        text = ask("ssh destination, like me@vm: ").strip()
        try:
            return str(prefs.BY_NAME["ssh"].check(text, "[remote]"))
        except UsageError as error:
            print(_sentence(error))


def _asking(question: str, choices: tuple[setup.Choice, ...]) -> Callable[[Ask, str], str]:
    return lambda ask, _mode: _ask_choice(ask, question, choices)


# How each answer in setup.QUESTIONS is asked. One missing fails at import.
_ASKERS: dict[str, Callable[[Ask, str], Any]] = {
    field: {
        "nvda_dir": _ask_nvda_dir,
        "keys_layout": _asking("Which keyboard layout does your NVDA use?", setup.LAYOUTS),
        "os": _asking("What does the other machine run?", setup.OSES),
        "ssh": _ask_ssh,
    }[field]
    for field in setup.LABELS
}


def _answers(ask: Ask, platform: str) -> tuple[setup.Answers, tuple[str, ...]]:
    """The answers, and which questions were asked."""
    named = paths.instance() is not None
    mode = _ask_mode(ask, named=named, platform=platform)
    if not setup.runs_here(mode, named=named):
        return setup.Answers(mode), ()
    asked: dict[str, Any] = {}
    for field in setup.QUESTIONS[mode]:
        if setup.asks(field, mode, asked, platform):
            asked[field] = _ASKERS[field](ask, mode)
    return setup.Answers(mode, **asked), tuple(asked)


def run_setup(ask: Ask, *, replace_without_asking: bool, platform: str = sys.platform) -> int:
    """Ask the questions, then write config.toml once. `ask` reads one answer."""
    if (mode := session.running_mode(paths.state_root())) is not None:
        raise AlreadyRunning(mode)
    path = paths.config_file()
    print("asc setup")
    if path.exists() and not replace_without_asking:
        question = f"This replaces your settings in {path}. Continue?"
        if not _ask_yes(ask, question, default=False):
            print("Nothing changed.")
            return 0
    print("Answer with a number or a name. Enter keeps the default.")
    print("Ctrl+C stops without saving.")
    answers, fields = _answers(ask, platform)
    _section(*setup.summary_lines(answers, fields), "")
    if not _ask_yes(ask, "Save?", default=True):
        print("Nothing saved.")
        return 0
    edit.save(path, setup.to_data(answers))
    print(f"Settings saved: {os.path.realpath(path)}")
    return 0


def confirm_uninstall(ask: Ask, planned: uninstall.Plan, *, keep_data: bool) -> bool | None:
    """Confirm removing a Linux tarball install, and ask whether to keep state when needed."""
    print("This removes asc:")
    for link in planned.links:
        print(f"  {link}")
    print(f"  {planned.root}")
    if not keep_data and planned.state_exists:
        print(f"Settings and data: {planned.state}")
        keep_data = _ask_yes(ask, "Keep your settings and data?", default=False)
    elif planned.state_exists:
        print(f"Keeping settings and data: {planned.state}")
    if not _ask_yes(ask, "Uninstall asc?", default=False):
        print("Nothing removed.")
        return None
    return keep_data
