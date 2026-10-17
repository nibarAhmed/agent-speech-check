"""The `asc` command line. The only module that prints.

`main` is the one place where errors become exit codes. Results go to stdout,
errors to stderr.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from agent_speech_check import __version__
from agent_speech_check.config import edit, prefs, setup
from agent_speech_check.config.prefs import MODES, WINDOWS
from agent_speech_check.config.targets import SANDBOX_PORT, Config, Target, load
from agent_speech_check.daemon import ipc, server, session
from agent_speech_check.errors import (
    EXIT_INTERRUPTED,
    AscError,
    BadArguments,
    BadSetting,
    InternalError,
    NeedsTerminal,
    NoCommand,
    TargetsRunning,
    UnrecognizedArguments,
    UsageError,
    bad_field,
)
from agent_speech_check.nvda import keyref, launcher, nvda_config
from agent_speech_check.nvda.nvda_config import Setting
from agent_speech_check.sandbox import firewall, wsb
from agent_speech_check.windows.paths import config_file, state_root, target_dir

DESCRIPTION = """\
Agent Speech Check: use NVDA the way a blind user does. Press keys, read what
NVDA says as text, and check it.

typical use:
  asc setup                    choose your settings (once)
  asc start                    start recording (watch mode: your own NVDA)
  asc status                   how NVDA should connect, and whether it is
  asc mark                     remember where the speech log is now
  asc wait "OK button"         block until NVDA says it (regex)
  asc speech                   what NVDA said since the mark
  asc stop

Speech is kept in memory only. Run asc COMMAND --help for details.
"""

EPILOG = "exit codes: see docs/cli.md"


@dataclass(frozen=True)
class Command:
    """One subcommand: its help, the function that runs it, and its own arguments.
    The table of them, `COMMANDS`, is at the end, after those functions."""

    summary: str
    details: str
    run: Callable[[argparse.Namespace], int]
    add_args: Callable[[argparse.ArgumentParser], None] | None = None
    # False for a command that picks its own target, or none.
    takes_target: bool = True


def _add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", metavar="NAME", help="target name (default: default)")


def _default(name: str) -> str:
    return f"default: the {name} setting, {edit.show(prefs.BY_NAME[name].default)} unless set"


def _timeout(parser: argparse.ArgumentParser, name: str = "timeout_ms") -> None:
    parser.add_argument("--timeout", type=int, metavar="MS", help=_default(name))


def _settle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--settle", type=int, metavar="MS", help=f"quiet period ({_default('settle_ms')})"
    )


def _start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=MODES, help="mode for an unlisted target")
    parser.add_argument(
        "--nvda-setting",
        action="append",
        default=[],
        metavar="SECTION.KEY=VALUE",
        help="an nvda.ini setting for the NVDA asc launches (repeatable)",
    )
    parser.add_argument(
        "--nvda-dir", metavar="DIR", help="desktop or sandbox: launch this portable NVDA"
    )
    parser.add_argument(
        "--window",
        choices=WINDOWS,
        help="sandbox: its window in the tray (default), on the taskbar, or shown",
    )
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help="shared mode: yes, send keys into my own session",
    )


def _speech_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--since", type=int, metavar="N", help="lines after N")


def _wait_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("regex", metavar="REGEX")
    _timeout(parser)
    parser.add_argument("--since", type=int, metavar="N", help="lines after N")


def _press_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("gestures", nargs="+", metavar="KEY")
    parser.add_argument("--expect", metavar="REGEX", help="exit 1 unless the speech matches")
    _settle(parser)
    _timeout(parser)


def _type_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", metavar="TEXT")
    _settle(parser)
    _timeout(parser)


def _keys_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("words", nargs="*", metavar="SEARCH")
    parser.add_argument(
        "--layout", choices=prefs.BY_NAME["keys_layout"].choices, help=_default("keys_layout")
    )
    parser.add_argument("--nvda-dir", metavar="DIR", help="read this NVDA's reference")
    parser.add_argument("--lang", help=f"reference language ({_default('keys_lang')})")
    parser.add_argument(
        "--braille",
        action=argparse.BooleanOptionalAction,
        help=f"include braille displays ({_default('keys_braille')})",
    )


def _launch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("app", metavar="CMD")
    parser.add_argument("--title", metavar="REGEX", help="wait until NVDA says this")
    _timeout(parser, "launch_timeout_ms")


def _get_nvda_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", dest="nvda_version", help=_default("nvda_version"))
    parser.add_argument("--installer", metavar="FILE", help="use this installer, no download")


def _sandbox_setup_args(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(mode="sandbox")


def _setup_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--yes", action="store_true", help="replace settings without asking")


CONFIG_HELP = {
    "list": "show every setting, and where its value comes from",
    "get": "print one setting's value",
    "set": "change a setting (a list takes several values)",
    "unset": "go back to the inherited value",
    "remove-target": "delete a target's settings",
    "path": "print where config.toml is",
}


def _config_args(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    for action, summary in CONFIG_HELP.items():
        sub = actions.add_parser(action, help=summary, description=summary)
        if action in ("get", "set", "unset"):
            sub.add_argument("name", metavar="NAME")
        if action == "set":
            sub.add_argument("values", nargs="+", metavar="VALUE")
        if action == "remove-target":
            sub.add_argument("name", metavar="TARGET")
        elif action != "path":
            _add_target(sub)


class _Parser(argparse.ArgumentParser):
    """Bad arguments raise `BadArguments`, so they print like every other error,
    without argparse's usage block. Subparsers are made of this class too, and
    each refuses its own leftovers, so the hint names the help that lists them."""

    def error(self, message: str) -> NoReturn:
        raise BadArguments(message, self.prog)

    def parse_known_args(  # type: ignore[override]
        self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        parsed, extra = super().parse_known_args(args, namespace)
        if extra:
            raise UnrecognizedArguments(extra, self.prog)
        return parsed, extra


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="asc",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"asc {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, command in COMMANDS.items():
        sub = commands.add_parser(
            name,
            help=command.summary,
            description=command.details,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        if command.takes_target:
            _add_target(sub)
        if command.add_args is not None:
            command.add_args(sub)
    return parser


# ---------------------------------------------------------------- output


def _connect_line(status: dict[str, Any]) -> str:
    return (
        f"connect NVDA: Remote Access, allow this computer to be controlled, "
        f"host {status['host']}, key {status['channel']}; "
        f"trust the certificate {status['fingerprint'][:16]}..."
    )


def _show_status(status: dict[str, Any]) -> None:
    keys = "keys allowed" if status["keys"] else "keys refused"
    print(f"target: {status['target']} ({status['mode']} mode, {keys})")
    print("recording: yes")
    print(f"nvda: {'connected' if status['connected'] else 'not connected'}")
    if not status["connected"]:
        print(_connect_line(status))
    print(f"browse/focus: {status['nvda_mode']}")
    print(f"speech: {status['lines']} lines kept, latest {status['latest']}, mark {status['mark']}")
    if status.get("panic_key"):
        print(f"panic key: {status['panic_key']} stops asc and releases keys")
    if status.get("sandbox"):
        print(f"sandbox: {status['sandbox']}")
    if status["last_error"]:
        print(f"last error: {status['last_error']}")


def _show_lines(lines: list[str], empty: str) -> None:
    print("\n".join(lines) if lines else empty)


def _show_said(lines: list[str]) -> None:
    _show_lines(lines, "(nothing said)")


# ---------------------------------------------------------------- commands


def _target(args: argparse.Namespace, settings: Config | None = None) -> Target:
    settings = settings or load(config_file())
    return settings.resolve(args.target, getattr(args, "mode", None))


def _with_defaults(args: argparse.Namespace, flags: dict[str, str]) -> Target:
    """The target. Each flag in `flags` left out is filled in from its setting."""
    settings = load(config_file())
    target = _target(args, settings)
    for flag, name in flags.items():
        if getattr(args, flag) is None:
            setattr(args, flag, settings.value(name, target.name)[0])
    return target


def _call(target: Target, cmd: str, raw: dict[str, Any], timeout_ms: int = 0) -> dict[str, Any]:
    request = ipc.parse_request(cmd, raw)
    timeout_s = timeout_ms / 1000 + session.STATUS_TIMEOUT_S
    return ipc.call(target_dir(target.name), target.name, request, timeout_s)


def cmd_start(args: argparse.Namespace) -> int:
    target = _target(args).with_nvda_dir(args.nvda_dir).with_window(args.window)
    given = tuple(Setting.parse(text) for text in args.nvda_setting)
    if given and not target.launches_nvda:
        raise BadSetting(args.nvda_setting[0], "only an NVDA that asc launches takes settings")
    settings = nvda_config.merge(target.settings, given)
    nvda_config.check(settings)
    target = target.with_settings(settings).with_consent(args.i_understand)
    status = session.start(target, target_dir(target.name))
    print(f"started: {target.name}")
    _show_status(status)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    target = _target(args)
    session.stop(target_dir(target.name), target.name)
    print(f"stopped: {target.name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _show_status(_call(_target(args), "status", {}))
    return 0


def cmd_speech(args: argparse.Namespace) -> int:
    result = _call(_target(args), "speech", {"since": args.since})
    if result["dropped"]:
        print(f"({result['dropped']} earlier lines dropped)")
    lines = [f"{n} {text}" for n, text in result["lines"]]
    _show_said(lines)
    return 0


_TIMINGS = {"settle": "settle_ms", "timeout": "timeout_ms"}


def cmd_wait(args: argparse.Namespace) -> int:
    target = _with_defaults(args, {"timeout": "timeout_ms"})
    raw = {"pattern": args.regex, "timeout_ms": args.timeout, "since": args.since}
    n, text = _call(target, "wait", raw, args.timeout)["line"]
    print(f"{n} {text}")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    print(f"mark: {_call(_target(args), 'mark', {})['mark']}")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    _call(_target(args), "clear", {})
    print("cleared")
    return 0


def cmd_press(args: argparse.Namespace) -> int:
    target = _with_defaults(args, _TIMINGS)
    raw = {
        "gestures": args.gestures,
        "expect": args.expect,
        "settle_ms": args.settle,
        "timeout_ms": args.timeout,
    }
    _show_said(_call(target, "press", raw, args.timeout)["lines"])
    return 0


def cmd_type(args: argparse.Namespace) -> int:
    target = _with_defaults(args, _TIMINGS)
    raw = {"text": args.text, "settle_ms": args.settle, "timeout_ms": args.timeout}
    _show_said(_call(target, "type", raw, args.timeout)["lines"])
    return 0


def _nvda_dirs(args: argparse.Namespace, target: Target) -> list[tuple[str, Path]]:
    if args.nvda_dir:
        return [("--nvda-dir", Path(args.nvda_dir))]
    if target.launches_portable or (target.launches_sandbox and target.nvda_dir):
        return [("the portable NVDA", launcher.folder_for(target.nvda_dir))]
    return keyref.candidates()


def cmd_keys(args: argparse.Namespace) -> int:
    flags = {"layout": "keys_layout", "lang": "keys_lang", "braille": "keys_braille"}
    target = _with_defaults(args, flags)
    path = keyref.reference(_nvda_dirs(args, target), args.lang)
    commands = keyref.parse(path.read_text(encoding="utf-8"))
    found = keyref.search(commands, args.words, args.layout, braille=args.braille)
    _show_lines([c.line(args.layout) for c in found], "(no match)")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    target = _with_defaults(args, {"timeout": "launch_timeout_ms"})
    raw = {"command": args.app, "title": args.title, "timeout_ms": args.timeout}
    _show_said(_call(target, "launch", raw, args.timeout + ipc.LAUNCH_START_MS)["lines"])
    return 0


def cmd_sandbox_setup(args: argparse.Namespace) -> int:
    wsb.require_available()
    target = _target(args)
    program = firewall.daemon_program()
    if firewall.rule_ok(target.port, program):
        print(f"firewall rule already in place for port {target.port}")
        return 0
    _add_firewall_rule(target.port, program)
    return 0


def _add_firewall_rule(port: int, program: str) -> None:
    print("asking Windows for admin rights to add one firewall rule")
    firewall.create_rule(port, program)
    print(f"firewall rule added: {firewall.RULE_NAME}, TCP {port}, {firewall.INTERFACE}")


def cmd_get_nvda(args: argparse.Namespace) -> int:
    # It goes into the download URL and a file name.
    if args.nvda_version is not None and not re.fullmatch(
        launcher.VERSION_PATTERN, args.nvda_version
    ):
        raise bad_field(UsageError, "--version", "an NVDA version like 2026.2", args.nvda_version)
    version = args.nvda_version or str(load(config_file()).value("nvda_version")[0])
    _make_portable(version, Path(args.installer) if args.installer else None)
    return 0


def _make_portable(version: str, installer: Path | None = None) -> None:
    """asc's own portable NVDA, from `installer` or a download of `version`."""
    launcher.refuse_if_running()
    if installer is None:
        print(f"downloading {launcher.installer_url(version)}")
        installer = launcher.download(version)
    exe = launcher.create_portable(installer, launcher.managed_dir())
    print(f"portable NVDA ready: {exe}")


def _running(target: str) -> bool:
    return session.is_running(target_dir(target), target)


def _config_change(settings: Config, args: argparse.Namespace) -> int:
    pref = prefs.find(args.name)
    where = edit.owner(settings, pref, args.target)
    if args.action == "set":
        data, value = edit.set_value(settings, pref, args.values, where)
        done = f"{pref.name} = {edit.show(value)} ({edit.label(where)})"
    else:
        data = edit.unset_value(settings, pref, where)
        done = f"{pref.name} unset ({edit.label(where)})"
    # Command defaults are read by each command, so only target settings wait.
    # Asked before saving, so a failed look leaves the file as it was.
    waits = where is not None and pref.scope == prefs.TARGET and _running(where)
    edit.save(config_file(), data)
    print(done)
    if waits:
        print(f"applies at the next asc start of {where}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.action == "path":
        # Not read, so it works on a file too broken to load.
        print(os.path.realpath(config_file()))
        return 0
    settings = load(config_file())
    if args.action == "list":
        for name, value, source in edit.entries(settings, args.target):
            print(f"{name} = {edit.show(value)} ({source})")
    elif args.action == "get":
        pref = prefs.find(args.name)
        values = {name: value for name, value, _ in edit.entries(settings, args.target)}
        print(edit.show(values[pref.name]))
    elif args.action == "remove-target":
        data = edit.remove_target(settings, args.name)
        if _running(args.name):
            raise TargetsRunning([args.name])
        edit.save(config_file(), data)
        print(f"removed: {args.name}")
    else:
        return _config_change(settings, args)
    return 0


# ---------------------------------------------------------------- setup


type Ask = Callable[[str], str]


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


def _offer(ask: Ask, question: str, action: Callable[[], None], later: str) -> None:
    """Do `action` now if the user agrees. Otherwise, or if it fails, name the
    command `later` that does it."""
    if _ask_yes(ask, question, default=True):
        try:
            action()
        except AscError as error:
            print(f"Not done. {_sentence(error)}")
        else:
            return
    print(f"Run {later} later.")


def _get_nvda_now(ask: Ask) -> None:
    if (launcher.managed_dir() / "nvda.exe").is_file():
        return
    print()
    question = "asc has no portable NVDA yet. Download it now?"
    _offer(ask, question, lambda: _make_portable(launcher.VERSION), "asc get-nvda")


def _firewall_now(ask: Ask) -> None:
    program = firewall.daemon_program()
    if firewall.rule_ok(SANDBOX_PORT, program):
        return
    _section("Sandbox mode needs one firewall rule, so the sandbox's NVDA can reach asc.")
    question = "Add it now? Windows asks for admin rights."
    _offer(ask, question, lambda: _add_firewall_rule(SANDBOX_PORT, program), "asc sandbox-setup")


def _ask_mode(ask: Ask) -> str:
    while True:
        mode = _ask_choice(ask, "How should asc work with NVDA?", setup.MODES)
        if mode == "sandbox":
            try:
                wsb.require_available()
            except AscError as error:
                print(f"{_sentence(error)} Pick another mode.")
                continue
        if mode == "desktop" and launcher.running():
            print("Note: desktop mode won't start while another NVDA runs.")
        if mode == "shared":
            print("Note: shared mode needs --i-understand at every asc start.")
            print(f"While it runs, {server.PANIC_KEY} stops asc and releases keys.")
        return mode


def _ask_nvda_dir(ask: Ask, mode: str) -> str | None:
    nvda_dir = None
    if _ask_choice(ask, "Which NVDA should asc run?", setup.NVDA_SOURCES) == "folder":
        nvda_dir = _ask_folder(ask, "Your portable NVDA folder: ", holding="nvda.exe")
    if nvda_dir is None and mode == "desktop":
        _get_nvda_now(ask)
    return nvda_dir


def _ask_share(ask: Ask, _mode: str) -> tuple[str, ...]:
    _section("Folders to share with the sandbox, one at a time.")
    share: list[str] = []
    while folder := _ask_folder(ask, "Folder, or Enter when done: "):
        share.append(folder)
    return tuple(share)


def _asking(question: str, choices: tuple[setup.Choice, ...]) -> Callable[[Ask, str], str]:
    return lambda ask, _mode: _ask_choice(ask, question, choices)


# How each answer in setup.QUESTIONS is asked. One missing fails at import.
_ASKERS: dict[str, Callable[[Ask, str], Any]] = {
    field: {
        "nvda_dir": _ask_nvda_dir,
        "share": _ask_share,
        "window": _asking("How should the sandbox window appear?", setup.WINDOWS),
        "keys_layout": _asking("Which keyboard layout does your NVDA use?", setup.LAYOUTS),
    }[field]
    for field in setup.LABELS
}


def _answers(ask: Ask) -> setup.Answers:
    mode = _ask_mode(ask)
    given = {field: _ASKERS[field](ask, mode) for field in setup.QUESTIONS[mode]}
    if mode == "sandbox":
        _firewall_now(ask)
    return setup.Answers(mode, **given)


def run_setup(ask: Ask, *, replace_without_asking: bool) -> int:
    """Ask the questions, then write config.toml once. `ask` reads one answer."""
    running = session.running_targets(state_root())
    if running:
        raise TargetsRunning(running)
    path = config_file()
    print("asc setup")
    if path.exists() and not replace_without_asking:
        question = f"This replaces your settings in {path}. Continue?"
        if not _ask_yes(ask, question, default=False):
            print("Nothing changed.")
            return 0
    print("Answer with a number or a name. Enter keeps the default.")
    print("Ctrl+C stops without saving.")
    answers = _answers(ask)
    _section(*setup.summary_lines(answers), "")
    if not _ask_yes(ask, "Save?", default=True):
        print("Nothing saved.")
        return 0
    edit.save(path, setup.to_data(answers))
    print(f"Settings saved: {os.path.realpath(path)}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise NeedsTerminal
    try:
        return run_setup(input, replace_without_asking=args.yes)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped, nothing saved.")
        return EXIT_INTERRUPTED


COMMANDS: dict[str, Command] = {
    "start": Command(
        "start recording for a target",
        """\
Start the background daemon for a target. Targets come from config.toml (see
asc config), or default to --mode (watch if left out).

watch mode: your own NVDA connects to asc and asc only listens. It never sends
keys. Connect once in NVDA (Tools, Remote Access, Connect): allow this computer
to be controlled, host and key as printed, and trust the certificate when asked.
The host, key and certificate stay the same for the target.

examples:
  asc start
  asc start --target app""",
        cmd_start,
        _start_args,
    ),
    "stop": Command(
        "stop recording and forget the speech",
        "Stop the daemon. Held keys are released. A daemon that doesn't answer is ended.",
        cmd_stop,
    ),
    "status": Command(
        "show whether asc is recording and NVDA is connected",
        "Show the target, whether NVDA is connected, how to connect it, and the\n"
        "browse/focus mode (unknown until NVDA first switches).",
        cmd_status,
    ),
    "speech": Command(
        "print what NVDA said since the mark",
        """\
Print what NVDA said, one numbered line per utterance, plus mode changes.
By default, lines after the last asc mark.

examples:
  asc speech
  asc speech --since 0       everything still kept
  asc speech --since 41      lines after 41""",
        cmd_speech,
        _speech_args,
    ),
    "wait": Command(
        "wait until NVDA says something matching REGEX",
        """\
Return as soon as an utterance after the mark (or --since) matches REGEX
(Python syntax, searched anywhere in the line; use (?i) to ignore case).
Exits 1 with what NVDA did say if nothing matched in time.

examples:
  asc wait "OK button"
  asc wait "(?i)saved" --timeout 10000""",
        cmd_wait,
        _wait_args,
    ),
    "mark": Command(
        "remember the current end of the speech log",
        "Print and set the mark.",
        cmd_mark,
    ),
    "clear": Command(
        "drop all kept speech",
        "Drop all kept speech. Line numbers keep counting.",
        cmd_clear,
    ),
    "press": Command(
        "press keys and print what NVDA said",
        """\
Send each gesture, wait until NVDA is quiet for --settle ms, and print only
the new speech: said: "..." lines, mode: lines, or (nothing said).
Keys are names joined by +, as NVDA writes them. Look commands up with asc keys.
Watch mode refuses keys (exit 4).

examples:
  asc press Tab
  asc press NVDA+Tab
  asc press H H --expect "level 2"
  asc press Control+Home NVDA+Down""",
        cmd_press,
        _press_args,
    ),
    "type": Command(
        "type text and print what NVDA said",
        """\
Type TEXT one character at a time, using this computer's keyboard layout.
Every character is checked first; if any can't be typed, nothing is (exit 2).
NVDA gets one --settle quiet period at the end. Watch mode refuses (exit 4).

examples:
  asc type "hello world"
  asc type "x@example.com" --settle 800""",
        cmd_type,
        _type_args,
    ),
    "keys": Command(
        "look up NVDA commands in NVDA's own reference",
        """\
Print commands from NVDA's Commands Quick Reference whose name, keys,
description or section contain every SEARCH word, one per line:
KEY  NAME: DESCRIPTION. Needs no running asc. The reference comes from the
NVDA the target uses: its portable copy for a desktop target or a sandbox
target with nvda_dir, otherwise your own NVDA (the running nvda.exe, the
uninstall registry entry, Program Files).
In browse mode, single letters jump to elements; add Shift to go back.

examples:
  asc keys heading
  asc keys report title
  asc keys --layout laptop current line""",
        cmd_keys,
        _keys_args,
    ),
    "launch": Command(
        "start an app and wait until NVDA speaks",
        """\
Start CMD and wait until NVDA says something matching --title (a regex), or
anything at all if --title is left out. Prints what NVDA said.
Default timeout 30000 ms. When asc runs NVDA in Windows Sandbox, CMD runs
inside the sandbox, which has no Notepad or Edge: start your own app from C:\\share\\<folder>.

examples:
  asc launch "C:\\share\\app\\run.cmd" --title "My App"
  asc launch "py -m myapp" --title "My App" --timeout 60000""",
        cmd_launch,
        _launch_args,
    ),
    "get-nvda": Command(
        "make the portable NVDA that desktop mode launches",
        """\
Download NVDA from NV Access and make a portable copy for desktop mode, in
%LOCALAPPDATA%\\agent-speech-check\\nvda\\portable. Refuses (exit 4) while any
NVDA runs, because the NVDA installer would restart it.

examples:
  asc get-nvda
  asc get-nvda --installer nvda_2026.2.exe""",
        cmd_get_nvda,
        _get_nvda_args,
    ),
    "setup": Command(
        "choose your settings, by answering questions",
        """Ask a few questions and save the answers in config.toml, for the default
target. Running it again replaces all your settings. It needs a terminal;
scripts and agents use asc config instead. Press Enter to keep a default, and
Ctrl+C to stop without saving.

examples:
  asc setup
  asc setup --yes          replace existing settings without asking""",
        cmd_setup,
        _setup_args,
        takes_target=False,
    ),
    "config": Command(
        "show or change one setting",
        """Show or change the settings in config.toml. Target settings go to --target,
or to the default target. Command defaults (settle_ms, timeout_ms, ...) go to
[defaults], or to one target with --target. A flag on a command beats both.

examples:
  asc config list
  asc config set settle_ms 800 --target app
  asc config set mode sandbox --target app      makes target app
  asc config set share C:\\src\\app C:\\data --target app
  asc config unset settle_ms --target app""",
        cmd_config,
        _config_args,
        takes_target=False,
    ),
    "sandbox-setup": Command(
        "allow the sandbox's NVDA to reach asc (asks for admin once)",
        """Sandbox mode runs NVDA in Windows Sandbox, which connects to asc on this
computer's Hyper-V Default Switch address. Windows Firewall blocks that until
one rule allows it. This command creates that rule after a Windows admin
prompt: inbound TCP only, only on the Default Switch, only the relay port
(16837 unless the target sets another), only for the program running asc.
Run it again after updating Python or asc, since the rule names the program.

examples:
  asc sandbox-setup
  asc sandbox-setup --target app""",
        cmd_sandbox_setup,
        _sandbox_setup_args,
    ),
}


def run(argv: Sequence[str] | None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["_daemon"] and len(args_list) == 2:  # noqa: PLR2004 - _daemon JSON
        # Hidden: how `asc start` runs the daemon.
        return server.run(args_list[1])
    if args_list[:1] == ["_sandbox_guard"] and len(args_list) == 3:  # noqa: PLR2004 - pid, id
        # Hidden: stops a sandbox once the process that started it is gone.
        wsb.guard(int(args_list[1]), args_list[2])
        return 0
    if args_list == ["_stop_all"]:
        # Hidden: how the installer stops every daemon before it replaces or
        # removes asc.exe. Only an installed asc.exe has other copies to wait for.
        image = Path(sys.executable) if getattr(sys, "frozen", False) else None
        for target in session.stop_all(state_root(), image):
            print(f"stopped: {target}")
        return 0
    args = build_parser().parse_args(args_list)
    if args.command is None:
        raise NoCommand
    return COMMANDS[args.command].run(args)


def _utf8_streams() -> None:
    """NVDA speech can hold any character. The Windows default code page can't
    print them all, so results and errors are always UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def _causes(error: BaseException) -> list[BaseException]:
    """The errors an ExceptionGroup (as a failed TaskGroup raises) wraps, or the error itself."""
    if isinstance(error, BaseExceptionGroup):
        return [cause for inner in error.exceptions for cause in _causes(inner)]
    return [error]


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_streams()
    try:
        return run(argv)
    except AscError as error:
        print(f"error: {error.message}", file=sys.stderr)
        if error.hint:
            print(f"hint: {error.hint}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except Exception as error:  # noqa: BLE001 - last resort: turn any bug into exit 6
        for cause in _causes(error):
            print(f"internal error: {type(cause).__name__}: {cause}", file=sys.stderr)
        # Real, not Store Python's redirected view, so other programs find it.
        print(f"logs: {os.path.realpath(state_root())}", file=sys.stderr)
        if os.environ.get("ASC_DEBUG") == "1":
            traceback.print_exc()
        return InternalError.exit_code
