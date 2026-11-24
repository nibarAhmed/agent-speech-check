"""The `asc` command line. Only it and `config/prompts.py` print.

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
from agent_speech_check.config import edit, prefs, prompts
from agent_speech_check.config.prefs import MODES
from agent_speech_check.config.targets import Config, Target, check_runnable, load
from agent_speech_check.daemon import ipc, server, session, state
from agent_speech_check.errors import (
    EXIT_INTERRUPTED,
    AscError,
    BadSetting,
    InternalError,
    NotRunning,
    OperationalError,
    OrcaNeedsRestart,
    UsageError,
    bad_field,
    numbered_lines,
)
from agent_speech_check.inputs.keyref import search as search_commands
from agent_speech_check.nvda import invite, keyref, launcher, nvda_config
from agent_speech_check.nvda.nvda_config import Setting
from agent_speech_check.orca import keyref as orca_keyref
from agent_speech_check.orca import link as orca_link
from agent_speech_check.system import paths
from agent_speech_check.system.paths import config_file, mode_dir, state_root
from agent_speech_check.system.wait import poll_until
from agent_speech_check.update import uninstall, updater

DESCRIPTION = (
    "Agent Speech Check: test apps with a real screen reader (NVDA on Windows,\n"
    "Orca on Linux). Press keys, read what it says as text, and check it."
)

EPILOG = """\
examples:
  asc setup
  asc start
  asc mark                         # prints mark: N
  asc wait --since N "OK"
  asc press Tab --expect "Submit"
  asc stop

Speech is kept in memory only. Run 'asc COMMAND --help' for command details.
Exit codes: 0 ok, 1 no match/timeout, 2 usage/config, 3 operational, 4 internal.
Docs: https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md#exit-codes"""


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """List subcommands directly under their section header without a redundant metavar line."""

    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            return self._join_parts([self._format_action(sub) for sub in action._get_subactions()])
        return super()._format_action(action)


@dataclass(frozen=True)
class Command:
    """One subcommand: its help, the function that runs it, and its own arguments.
    The table of them, `COMMANDS`, is at the end, after those functions."""

    summary: str
    details: str
    run: Callable[[argparse.Namespace], int]
    add_args: Callable[[argparse.ArgumentParser], None] | None = None
    examples: str = ""
    # True for a command that works on one mode's settings.
    takes_mode: bool = False
    windows_only: bool = False
    linux_only: bool = False


def _add_instance(parser: argparse.ArgumentParser, default: str | None) -> None:
    parser.add_argument(
        "--instance",
        metavar="NAME",
        default=default,
        help="run an isolated instance with its own config and daemon (default: ASC_INSTANCE)",
    )


def _add_mode(parser: argparse.ArgumentParser, default: object = None) -> None:
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=default,
        help="mode to use (default: mode setting, else watch on Windows, desktop on Linux)",
    )


def _default(name: str) -> str:
    return f"default: {name} setting, {edit.show(prefs.BY_NAME[name].default)}"


def _timeout(parser: argparse.ArgumentParser, name: str = "timeout_ms") -> None:
    parser.add_argument(
        "--timeout",
        type=int,
        metavar="MS",
        help=f"maximum wait time in ms ({_default(name)})",
    )


def _settle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--settle",
        type=int,
        metavar="MS",
        help=f"quiet period after input in ms ({_default('settle_ms')})",
    )


def _start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--nvda-setting",
        action="append",
        default=[],
        metavar="SECTION.KEY=VALUE",
        help="an nvda.ini setting for the NVDA asc launches (repeatable)",
    )
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help="shared mode: confirm sending keys into your own Windows session",
    )


def _status_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--nvda-ini",
        action="store_true",
        help=(
            "remote mode (Windows): print an unattended nvda.ini "
            "(save as UTF-8 in that NVDA's config folder while NVDA is closed)"
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="list every configured or running instance (exits 0 even when nothing runs)",
    )


def _since(
    parser: argparse.ArgumentParser, text: str = "read lines spoken after line N (required)"
) -> None:
    parser.add_argument("--since", type=int, metavar="N", required=True, help=text)


def _wait_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("regex", metavar="REGEX", help="Python regex to match against spoken lines")
    _timeout(parser)
    _since(parser, "match lines spoken after line N; use asc mark first (required)")


def _press_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "gestures",
        nargs="+",
        metavar="KEY",
        help="key gesture(s) joined with +, like Tab or NVDA+Tab",
    )
    parser.add_argument(
        "--expect", metavar="REGEX", help="wait up to --timeout for a match, else exit 1"
    )
    _settle(parser)
    _timeout(parser)


def _stop_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--all", action="store_true", help="stop every instance's daemon and skip the update check"
    )


def _type_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", metavar="TEXT", help="text to type one character at a time")
    _settle(parser)
    _timeout(parser)


def _keys_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "words", nargs="*", metavar="SEARCH", help="words that must all appear in matching entries"
    )
    parser.add_argument(
        "--layout",
        choices=prefs.BY_NAME["keys_layout"].choices,
        help=f"keyboard layout ({_default('keys_layout')})",
    )
    parser.add_argument(
        "--lang", default="en", help="reference language, falling back to en (default: en)"
    )
    parser.add_argument(
        "--braille",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="include braille display commands (default: off)",
    )


def _launch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("app", metavar="CMD", help="command line to start")
    parser.add_argument("--title", metavar="REGEX", help="wait until the screen reader says this")
    _timeout(parser, "launch_timeout_ms")


def _get_nvda_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        dest="nvda_version",
        help="NVDA version to download, like 2026.2 (default: latest)",
    )
    parser.add_argument(
        "--installer",
        metavar="FILE",
        help="local NVDA installer file to use instead of downloading",
    )


def _setup_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--yes", action="store_true", help="replace existing settings without asking"
    )


def _uninstall_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="keep settings and data in ~/.local/state/agent-speech-check",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="uninstall without asking (deletes settings and data unless --keep-data)",
    )


CONFIG_HELP = {
    "list": "show every setting and where its value comes from",
    "get": "print one setting's value",
    "set": "change a setting (list settings accept multiple values)",
    "unset": "remove a setting and revert to the default",
    "path": "print where config.toml is",
}


def _config_args(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(title="actions", dest="action", metavar="ACTION")
    for action, summary in CONFIG_HELP.items():
        description = summary[:1].upper() + summary[1:] + "."
        sub = actions.add_parser(
            action, help=summary, description=description, formatter_class=_HelpFormatter
        )
        _add_instance(sub, argparse.SUPPRESS)
        if action in ("get", "set", "unset"):
            sub.add_argument(
                "name", metavar="NAME", help="setting name (run 'asc config list' to see all)"
            )
        if action == "set":
            sub.add_argument(
                "values",
                nargs="+",
                metavar="VALUE",
                help="new value (repeat for list settings like nvda_settings)",
            )
        if action != "path":
            _add_mode(sub, argparse.SUPPRESS)


class _Parser(argparse.ArgumentParser):
    """Bad arguments raise `UsageError`, so they print like every other error,
    without argparse's usage block. Subparsers are made of this class too, and
    each refuses its own leftovers, so the hint names the help that lists them."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message, hint=f"{self.prog} --help")

    def parse_known_args(  # type: ignore[override]
        self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> tuple[argparse.Namespace, list[str]]:
        parsed, extra = super().parse_known_args(args, namespace)
        if extra:
            raise UsageError(
                f"unrecognized arguments: {' '.join(extra)}", hint=f"{self.prog} --help"
            )
        return parsed, extra


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="asc",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=_HelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"asc {__version__}")
    _add_instance(parser, None)
    commands = parser.add_subparsers(title="commands", dest="command", metavar="COMMAND")
    for name, command in COMMANDS.items():
        sub = commands.add_parser(
            name,
            help=command.summary,
            description=command.details,
            epilog=command.examples or None,
            formatter_class=_HelpFormatter,
        )
        # Suppressed, so a subcommand without it keeps the one given before it.
        _add_instance(sub, argparse.SUPPRESS)
        if command.takes_mode:
            _add_mode(sub)
        if command.add_args is not None:
            command.add_args(sub)
    return parser


# ---------------------------------------------------------------- output


def _connect_line(status: dict[str, Any]) -> str:
    return (
        f"connect NVDA: Remote Access, allow this computer to be controlled, "
        f"host {status['host']}:{status['port']}, key {status['channel']}; "
        f"trust the certificate {status['fingerprint'][:16]}..."
    )


def _show_status(status: dict[str, Any]) -> None:
    keys = "keys allowed" if status["keys"] else "keys refused"
    print(f"mode: {status['mode']} ({keys})")
    print("recording: yes")
    reader = status["reader"]
    print(f"{reader}: {'connected' if status['connected'] else 'not connected'}")
    if status["ssh"]:
        tunnel = status["tunnel"]
        if tunnel is None:
            up = ""
        elif tunnel["up"]:
            up = ", tunnel up"
        else:
            up = ", tunnel down" + (f" ({tunnel['error']})" if tunnel["error"] else "")
        print(f"over ssh: {status['ssh']}{up}")
    # Nothing listens for Orca: asc starts or records it itself.
    if not status["connected"] and status["port"] is not None:
        print(_connect_line(status))
    print(f"browse/focus: {status['browse_focus']}")
    print(f"speech: {status['lines']} lines kept, latest {status['latest']}")
    if status.get("panic_key"):
        print(f"panic key: {status['panic_key']} stops asc and releases keys")
    if status["last_error"]:
        print(f"last error: {status['last_error']}")


def _show_lines(lines: list[str], empty: str) -> None:
    print("\n".join(lines) if lines else empty)


def _show_reply(reply: dict[str, Any]) -> None:
    print(numbered_lines(reply["lines"], reply["after"], reply["dropped"]))


# ---------------------------------------------------------------- commands


def _target(args: argparse.Namespace, settings: Config | None = None) -> Target:
    settings = settings or load(config_file())
    return settings.resolve(args.mode)


def _with_defaults(args: argparse.Namespace, flags: dict[str, str]) -> Config:
    """The settings. Each flag in `flags` left out is filled in from its setting."""
    settings = load(config_file())
    for flag, name in flags.items():
        if getattr(args, flag) is None:
            setattr(args, flag, settings.value(name)[0])
    return settings


def _call(cmd: str, raw: dict[str, Any], timeout_ms: int = 0) -> dict[str, Any]:
    """Send one request to the daemon, whatever its mode."""
    request = ipc.parse_request(cmd, raw)
    timeout_s = timeout_ms / 1000 + session.STATUS_TIMEOUT_S
    return session.call(state_root(), request, timeout_s)


# NVDA tries a relay it lost again every 5 s, and Windows takes about 2 s more to
# refuse a connection while nothing listens.
REJOIN_S = 10.0
# For the user to answer NVDA's question.
ANSWER_S = 60.0
JOIN_POLL_S = 0.5


def _connect_own_reader(folder: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Get the user's running NVDA to join without its Connect dialog.

    An NVDA that joined this relay before comes back by itself. Otherwise asc
    hands it a link, and NVDA asks the user to confirm.
    """
    if not launcher.running():
        return status

    def joined() -> bool:
        return bool(session.status(folder)["connected"])

    if poll_until(joined, REJOIN_S, JOIN_POLL_S):
        return session.status(folder)
    link = invite.link(f"{status['host']}:{status['port']}", status["channel"])
    if not (invite.registered() and invite.send(link)):
        return status
    # Now, not at exit: the user has to act while this command waits.
    print(
        "sent NVDA a link: answer Yes in NVDA, and trust the certificate the first time",
        flush=True,
    )
    poll_until(joined, ANSWER_S, JOIN_POLL_S)
    return session.status(folder)


def cmd_start(args: argparse.Namespace) -> int:
    config = load(config_file())
    target = _target(args, config)
    given = tuple(Setting.parse(text) for text in args.nvda_setting)
    if given and not target.launches_nvda:
        raise BadSetting(args.nvda_setting[0], "only an NVDA that asc launches takes settings")
    settings = nvda_config.merge(target.settings, given)
    nvda_config.check(settings)
    target = target.with_settings(settings).with_consent(args.i_understand)
    if target.launches_nvda and not launcher.running():
        _offer_update(target.nvda_dir)
    folder = mode_dir(target.mode)
    try:
        status = session.start(target, folder)
    except OrcaNeedsRestart:
        if not sys.stdin.isatty():
            raise
        if not prompts.confirm_orca_restart(input):
            cmd = orca_link.restart_command(orca_link.watch_secret_file())
            raise OperationalError(
                "asc can't hear your Orca: it wasn't started with asc's secret",
                hint=f"restart Orca for asc: {cmd}",
            ) from None
        status = session.start(target.with_restart_orca("always"), folder)
    if status.get("restarted"):
        print("restarted Orca with asc's recording secret", flush=True)
    print(f"started: {target.mode}", flush=True)
    if target.own_reader and target.reader == "nvda" and config.value("connect")[0]:
        status = _connect_own_reader(folder, status)
    _show_status(status)
    return 0


def _stop_every(roots: list[Path], images: Sequence[Path] = ()) -> int:
    """Stop every daemon under `roots`, print each one stopped, and return how many."""
    stopped = 0
    for folder in session.stop_all(roots, images):
        where = folder.relative_to(paths.base_root()).as_posix()
        print(f"stopped: {where}")
        stopped += 1
    return stopped


def cmd_stop(args: argparse.Namespace) -> int:
    roots = paths.every_root() if args.all else [state_root()]
    # A state file left behind counts as something to clear, not as nothing running.
    found = any(state.folders(root) for root in roots)
    if args.all:
        # Before asc is deleted: no update check may run, or start.
        updater.stop_check()
    stopped = _stop_every(roots)
    if not (found or stopped):
        raise NotRunning
    if not args.all:
        updater.start_if_due()
    return 0


def nvda_ini(status: dict[str, Any]) -> str:
    """The nvda.ini that connects a remote NVDA to this relay: desktop mode's base config."""
    if status["mode"] != "remote" or status["port"] is None:
        if status["reader"] == "orca":
            raise OperationalError("--nvda-ini is for NVDA; Orca needs no nvda.ini")
        raise OperationalError(
            f"--nvda-ini is for remote mode only; asc runs in {status['mode']} mode",
            hint="asc status shows how to connect NVDA",
        )
    relay = nvda_config.RelayAddress(
        status["host"], status["port"], status["channel"], status["fingerprint"]
    )
    return nvda_config.render(relay)


def _remote_label(os_name: str | None, ssh: str | None) -> str:
    parts = [part for part in (os_name, ssh) if part]
    return f"remote ({', '.join(parts)})" if parts else "remote"


def _instance_summary(root: Path) -> str:
    name = "default" if root == paths.base_root() else root.name
    current, folder_mode = session.running_status(root)
    if current is not None:
        mode = current["mode"]
        if mode == "remote":
            os_name = "linux" if current["reader"] == "orca" else "windows"
            mode_text = _remote_label(os_name, current["ssh"])
        else:
            mode_text = mode
        conn = "connected" if current["connected"] else "not connected"
        return f"{name}: {mode_text}, running, {current['reader']}: {conn}"
    if folder_mode is not None:
        return f"{name}: {folder_mode}, not answering"
    try:
        settings = load(root / "config.toml")
    except AscError as error:
        return f"{name}: bad config ({error.message})"
    if "mode" in settings.data:
        mode = settings.mode
    elif name != "default" and len(settings.modes) == 1:
        mode = next(iter(settings.modes))
    else:
        mode = settings.mode
    if mode == "remote":
        target = settings.resolve("remote")
        mode_text = _remote_label(target.os, target.ssh)
    else:
        mode_text = mode
    return f"{name}: {mode_text}, not running"


def cmd_status(args: argparse.Namespace) -> int:
    if args.all:
        for root in paths.every_root():
            print(_instance_summary(root))
        return 0
    status = _call("status", {})
    if args.nvda_ini:
        print(nvda_ini(status), end="")
    else:
        _show_status(status)
    return 0


def cmd_speech(args: argparse.Namespace) -> int:
    result = _call("speech", {"since": args.since})
    print(numbered_lines(result["lines"], args.since, result["dropped"]))
    return 0


_TIMINGS = {"settle": "settle_ms", "timeout": "timeout_ms"}


def cmd_wait(args: argparse.Namespace) -> int:
    _with_defaults(args, {"timeout": "timeout_ms"})
    raw = {"pattern": args.regex, "timeout_ms": args.timeout, "since": args.since}
    n, text = _call("wait", raw, args.timeout)["line"]
    print(f"{n} {text}")
    return 0


def cmd_mark(_: argparse.Namespace) -> int:
    print(f"mark: {_call('mark', {})['mark']}")
    return 0


def cmd_press(args: argparse.Namespace) -> int:
    _with_defaults(args, _TIMINGS)
    raw = {
        "gestures": args.gestures,
        "expect": args.expect,
        "settle_ms": args.settle,
        "timeout_ms": args.timeout,
    }
    _show_reply(_call("press", raw, args.timeout))
    return 0


def cmd_type(args: argparse.Namespace) -> int:
    _with_defaults(args, _TIMINGS)
    raw = {"text": args.text, "settle_ms": args.settle, "timeout_ms": args.timeout}
    _show_reply(_call("type", raw, args.timeout))
    return 0


def _nvda_dirs(target: Target) -> tuple[list[tuple[str, Path]], str]:
    """Where to look for the mode's NVDA, and the hint if it isn't there."""
    if target.launches_nvda:
        folder = launcher.folder_for(target.nvda_dir)
        return [("the portable NVDA", folder)], launcher.portable_hint(folder)
    return keyref.candidates(), "start your NVDA"


def cmd_keys(args: argparse.Namespace) -> int:
    # It names a folder, so nothing like ..\ may get in.
    if not re.fullmatch(r"[A-Za-z]{2,3}(_[A-Za-z]{2,4})?", args.lang):
        raise bad_field(UsageError, "--lang", "a language code like en or pt_BR", args.lang)
    target = _target(args, _with_defaults(args, {"layout": "keys_layout"}))
    check_runnable(target)
    if target.reader == "orca":
        commands = orca_keyref.commands(args.lang, target.ssh)
    elif target.ssh is not None:
        commands = keyref.parse(keyref.remote_reference(target.ssh, args.lang))
    else:
        nvda_dirs, hint = _nvda_dirs(target)
        path = keyref.reference(nvda_dirs, args.lang, hint)
        commands = keyref.parse(path.read_text(encoding="utf-8"))
    found = search_commands(commands, args.words, args.layout, braille=args.braille)
    _show_lines(list(dict.fromkeys(c.line(args.layout) for c in found)), "(no match)")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    _with_defaults(args, {"timeout": "launch_timeout_ms"})
    raw = {
        "command": args.app,
        "title": args.title,
        "timeout_ms": args.timeout,
        "cwd": str(Path.cwd()),
    }
    _show_reply(_call("launch", raw, args.timeout + ipc.LAUNCH_START_MS))
    return 0


def cmd_get_nvda(args: argparse.Namespace) -> int:
    version = args.nvda_version
    # It goes into the download URL and a file name.
    if version is not None and not re.fullmatch(launcher.VERSION_PATTERN, version):
        raise bad_field(UsageError, "--version", "an NVDA version like 2026.2", version)
    prompts.make_portable(version, Path(args.installer) if args.installer else None)
    return 0


def _offer_update(nvda_dir: str | None) -> None:
    """Before desktop mode starts its NVDA, say if NV Access has a newer one."""
    release = launcher.update_for(launcher.folder_for(nvda_dir) / "nvda.exe")
    if release is not None:
        prompts.say_update(release, nvda_dir, input if sys.stdin.isatty() else None)


def _config_change(settings: Config, args: argparse.Namespace) -> int:
    pref = prefs.find(args.name)
    where = edit.owner(settings, pref, args.mode)
    if args.action == "set":
        data, value = edit.set_value(settings, pref, args.values, where)
        done = f"{pref.name} = {edit.show(value)} ({edit.label(where)})"
    else:
        data = edit.unset_value(settings, pref, where)
        done = f"{pref.name} unset ({edit.label(where)})"
    # Command defaults are read by each command, so only a running mode's settings
    # and the panic key, which the daemon takes at its start, wait.
    # Asked before saving, so a failed look leaves the file as it was.
    if pref.name == "panic_key":
        waiting = session.running_mode(state_root()) is not None
    else:
        waiting = where is not None and session.is_running(mode_dir(where))
    edit.save(config_file(), data)
    print(done)
    if waiting:
        print("applies at the next asc start")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    action = args.action or "list"
    if action == "path":
        if args.mode is not None:
            raise UsageError(
                f"unrecognized arguments: --mode {args.mode}", hint="asc config path --help"
            )
        # Not read, so it works on a file too broken to load.
        print(os.path.realpath(config_file()))
        return 0
    settings = load(config_file())
    if action == "list":
        for name, value, source in edit.entries(settings, args.mode):
            print(f"{name} = {edit.show(value)} ({source})")
    elif action == "get":
        pref = prefs.find(args.name)
        values = {name: value for name, value, _ in edit.entries(settings, args.mode)}
        print(edit.show(values[pref.name]))
    else:
        return _config_change(settings, args)
    return 0


# ---------------------------------------------------------------- setup


def cmd_setup(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty():
        raise UsageError(
            "asc setup asks questions, so it needs a terminal",
            hint="use asc config set to change one setting",
        )
    try:
        return prompts.run_setup(input, replace_without_asking=args.yes)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped, nothing saved.")
        return EXIT_INTERRUPTED


def cmd_uninstall(args: argparse.Namespace) -> int:
    planned = uninstall.plan()
    keep_data = args.keep_data
    if not args.yes:
        if not sys.stdin.isatty():
            raise UsageError(
                "asc uninstall asks to confirm, so it needs a terminal",
                hint="pass --yes to uninstall without prompts",
            )
        try:
            chosen = prompts.confirm_uninstall(input, planned, keep_data=keep_data)
        except (KeyboardInterrupt, EOFError):
            print("\nNothing removed.")
            return EXIT_INTERRUPTED
        if chosen is None:
            return 0
        keep_data = chosen
    updater.stop_check()
    _stop_every(paths.every_root(), updater.installed_images(planned.exe))
    for path in uninstall.remove_files(planned, keep_data=keep_data):
        print(f"removed: {path}")
    if keep_data and planned.state_exists:
        print(f"kept: {planned.state}")
    if planned.has_orca_secret:
        print("Note: restart Orca or log out so Orca drops asc's recording secret.")
    return 0


COMMANDS: dict[str, Command] = {
    "start": Command(
        "start recording",
        """\
Start the background daemon in --mode or the configured default mode (watch on
Windows, desktop on Linux) using its settings from config.toml (see asc config).
One daemon runs per instance and serves all other commands.

modes:
  watch    record your own running screen reader without sending keys
           (Windows: invites NVDA and waits up to 1 min for approval; disable
           with asc config set connect false. Linux: asks in a terminal before
           restarting Orca with asc's secret; configure with restart_orca.)
  desktop  start asc's own screen reader (portable NVDA on Windows, hidden
           Orca on Linux)
  shared   send keys through your own running NVDA on Windows (requires
           --i-understand every time)
  remote   reach NVDA or Orca on another machine over ssh (requires --instance;
           os windows tunnels to 127.0.0.1 for an NVDA you start there, os
           linux starts Orca there)""",
        cmd_start,
        _start_args,
        examples="""\
examples:
  asc start
  asc start --mode desktop
  asc start --mode remote --instance vm1""",
        takes_mode=True,
    ),
    "stop": Command(
        "stop recording and forget the speech",
        "Stop this instance's daemon (regardless of mode) and release any held keys.\n"
        "Unresponsive daemons are terminated.",
        cmd_stop,
        _stop_args,
        examples="""\
examples:
  asc stop
  asc stop --all""",
    ),
    "status": Command(
        "show recording and screen reader connection status",
        """\
Show the active mode, whether NVDA or Orca is connected, how to connect NVDA,
and the browse/focus mode (unknown until the screen reader first switches).""",
        cmd_status,
        _status_args,
        examples="""\
examples:
  asc status
  asc status --all
  asc status --nvda-ini --instance vm1""",
    ),
    "speech": Command(
        "print what the screen reader said after a line",
        """\
Print what the screen reader said after line N, one numbered line per utterance,
plus mode changes. Take N from asc mark, or from the last line you read.""",
        cmd_speech,
        _since,
        examples="""\
examples:
  asc speech --since 0       everything still kept
  asc speech --since 41      lines after 41""",
    ),
    "wait": Command(
        "wait until speech matches REGEX",
        """\
Wait until an utterance after line N matches REGEX (Python regex, searched
anywhere in the line; use (?i) to ignore case), then print it with its line
number (use that number as the next --since). Exits 1 with what was said if
nothing matches before the timeout.""",
        cmd_wait,
        _wait_args,
        examples="""\
examples:
  asc wait --since 40 "OK button"
  asc wait --since 40 "(?i)saved" --timeout 10000""",
    ),
    "mark": Command(
        "print the latest line number",
        "Print the number of the latest line: mark: N. Run it before an action, then\n"
        "pass N to wait or speech as --since N. Nothing is stored.",
        cmd_mark,
    ),
    "press": Command(
        "press keys and print what was said",
        """\
Send each gesture, wait for --settle ms of quiet, and print only the new speech
as numbered lines, or (nothing said after N). Pass the last printed line number
(or N) as the next --since for wait or speech.
With --expect, asc waits up to --timeout for a match before exiting 1.
Keys are names joined by +, as NVDA writes them. Look commands up with asc keys.
Watch mode refuses keys.""",
        cmd_press,
        _press_args,
        examples="""\
examples:
  asc press Tab
  asc press NVDA+Tab
  asc press H H --expect "level 2"
  asc press Control+Home NVDA+Down""",
    ),
    "type": Command(
        "type text and print what was said",
        """\
Type TEXT one character at a time using this computer's keyboard layout
(with Orca, each character is sent directly). Every character is checked first;
if any cannot be typed, nothing is sent (exit 2). Waits for one --settle quiet
period after typing, then prints new speech like press.
Watch mode refuses input.""",
        cmd_type,
        _type_args,
        examples="""\
examples:
  asc type "hello world"
  asc type "x@example.com" --settle 800""",
    ),
    "keys": Command(
        "look up screen reader commands in its reference",
        """\
Search the screen reader's command reference for entries whose name, keys,
description, or section contain all SEARCH words (no running asc needed).
Prints one match per line: KEY  NAME: DESCRIPTION.
Reads NVDA's Commands Quick Reference (from the portable copy in desktop mode,
otherwise your running or installed NVDA) or Orca's help files in
$XDG_DATA_DIRS. Remote mode reads from the remote machine.
In browse mode, single letters jump to elements; add Shift to move backward.""",
        cmd_keys,
        _keys_args,
        examples="""\
examples:
  asc keys heading
  asc keys report title
  asc keys --layout laptop current line""",
        takes_mode=True,
    ),
    "launch": Command(
        "start an app and wait until the screen reader speaks",
        """\
Start CMD and wait until the screen reader says text matching --title (a regex),
or any speech if --title is omitted. Prints what was said, numbered like press.
Refused in remote mode with os windows: start the app there, then use asc wait.""",
        cmd_launch,
        _launch_args,
        examples="""\
examples:
  asc launch "notepad.exe" --title "Notepad"
  asc launch "py -m myapp" --title "My App" --timeout 60000""",
    ),
    "get-nvda": Command(
        "download a portable NVDA for desktop mode (Windows)",
        """\
Download NVDA from NV Access and extract a portable copy for desktop mode to
%LOCALAPPDATA%\\agent-speech-check\\nvda\\portable. Defaults to the latest
release (or the last cached installer when offline). Refuses while any NVDA is
running, because running the installer would restart it.""",
        cmd_get_nvda,
        _get_nvda_args,
        examples="""\
examples:
  asc get-nvda
  asc get-nvda --version 2026.2
  asc get-nvda --installer nvda_2026.2.exe""",
        windows_only=True,
    ),
    "setup": Command(
        "configure settings interactively",
        """\
Ask a few questions and save the answers in config.toml: your mode and its
settings. Running it again replaces all your settings. Requires a terminal;
scripts and agents should use asc config instead. Press Enter to keep a default,
or Ctrl+C to stop without saving.""",
        cmd_setup,
        _setup_args,
        examples="""\
examples:
  asc setup
  asc setup --yes          replace existing settings without asking""",
    ),
    "config": Command(
        "show or change settings in config.toml",
        """\
Show or change settings in config.toml. With no ACTION, prints every setting
(same as asc config list). Mode-specific settings (port, nvda_dir, etc.) apply
to --mode, or to the configured default mode if --mode is omitted. Global
settings and command defaults (settle_ms, timeout_ms, etc.) do not take --mode.
Command-line flags override command defaults.""",
        cmd_config,
        _config_args,
        examples="""\
examples:
  asc config
  asc config list
  asc config set mode desktop
  asc config set nvda_dir C:\\nvda --mode desktop
  asc config set settle_ms 800
  asc config unset settle_ms""",
        takes_mode=True,
    ),
    "uninstall": Command(
        "remove a Linux release install of asc",
        """\
Remove an asc installation unpacked from a Linux release tarball (the files
under ~/.local/share/agent-speech-check and the asc symlink pointing into it).
Stops every running daemon and background update check first.

Asks whether to keep settings and data in ~/.local/state/agent-speech-check
(or $XDG_STATE_HOME/agent-speech-check). Pass --keep-data to keep them without
asking, or --yes to delete everything without prompts. On Windows, uninstall
Agent Speech Check from Settings > Apps instead.""",
        cmd_uninstall,
        _uninstall_args,
        examples="""\
examples:
  asc uninstall
  asc uninstall --keep-data
  asc uninstall --yes""",
        linux_only=True,
    ),
}


def run(argv: Sequence[str] | None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    # Checked before anything, so even the daemon never works in a folder it shouldn't.
    paths.instance()
    if args_list[:1] == ["_daemon"] and len(args_list) == 2:  # noqa: PLR2004 - _daemon JSON
        # Hidden: how `asc start` runs the daemon.
        return server.run(args_list[1])
    if args_list == ["_stop_all"]:
        # Hidden: how the installer stops every daemon before it replaces or
        # removes asc.exe. Only an installed asc.exe has other copies to wait for.
        images = (
            updater.installed_images(Path(sys.executable)) if getattr(sys, "frozen", False) else ()
        )
        updater.stop_check()
        _stop_every(paths.every_root(), images)
        return 0
    if args_list == ["_update"]:
        # Hidden: the background update check that `asc stop` starts.
        updater.run()
        return 0
    # The hidden commands above stay open: the installer runs _stop_all.
    if updater.installing():
        raise OperationalError("asc is installing an update", hint="try again in a minute")
    args = build_parser().parse_args(args_list)
    if args.instance is not None:
        # In the environment, so the daemon asc starts works on it too.
        os.environ[paths.INSTANCE_VAR] = paths.check_instance(args.instance)
    if args.command is None:
        raise UsageError("no command given", hint="asc --help")
    command = COMMANDS[args.command]
    if command.windows_only and sys.platform != "win32":
        raise UsageError(
            f"asc {args.command} needs Windows",
            hint="on Linux, asc start runs Orca here, or remote mode reaches another machine",
        )
    if command.linux_only and sys.platform != "linux":
        raise UsageError(
            f"asc {args.command} is for Linux installs",
            hint="on Windows, uninstall Agent Speech Check in Settings > Apps",
        )
    return command.run(args)


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
    except Exception as error:  # noqa: BLE001 - last resort: turn any bug into exit 4
        for cause in _causes(error):
            print(f"internal error: {type(cause).__name__}: {cause}", file=sys.stderr)
        # Real, not Store Python's redirected view, so other programs find it.
        print(f"logs: {os.path.realpath(state_root())}", file=sys.stderr)
        if os.environ.get("ASC_DEBUG") == "1":
            traceback.print_exc()
        return InternalError.exit_code
