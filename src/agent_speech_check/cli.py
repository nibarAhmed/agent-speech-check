"""The `asc` command line. The only module that prints.

`main` is the one place where errors become exit codes. Results go to stdout,
errors to stderr.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent_speech_check import __version__, daemon, firewall, ipc, keyref, nvda, sandbox, session
from agent_speech_check.errors import (
    EXIT_INTERRUPTED,
    AscError,
    BadSetting,
    InternalError,
    NoCommand,
)
from agent_speech_check.nvda_config import Relay, Setting, render
from agent_speech_check.paths import state_root, target_dir, targets_file
from agent_speech_check.targets import MODES, Target, resolve

DESCRIPTION = """\
Agent Speech Check: use NVDA the way a blind user does. Press keys, read what
NVDA says as text, and check it.

typical use:
  asc start                    start recording (watch mode: your own NVDA)
  asc status                   how NVDA should connect, and whether it is
  asc mark                     remember where the speech log is now
  asc wait "OK button"         block until NVDA says it (regex)
  asc speech                   what NVDA said since the mark
  asc stop

Speech is kept in memory only. Run asc COMMAND --help for details.
"""

EPILOG = """\
exit codes:
  0    OK
  1    a wait or --expect failed
  2    usage error
  3    not connected, or no target
  4    refused
  5    protocol error
  6    internal error (a bug in asc)
  130  interrupted (Ctrl+C)
"""

HELP: dict[str, tuple[str, str]] = {
    "start": (
        "start recording for a target",
        """\
Start the background daemon for a target. Targets come from targets.toml in
the state folder, or default to --mode (watch if left out).

watch mode: your own NVDA connects to asc and asc only listens. It never sends
keys. Connect once in NVDA (Tools, Remote Access, Connect): allow this computer
to be controlled, host and key as printed, and trust the certificate when asked.
The host, key and certificate stay the same for the target.

examples:
  asc start
  asc start --target app""",
    ),
    "stop": ("stop recording and forget the speech", "Stop the daemon. Held keys are released."),
    "status": (
        "show whether asc is recording and NVDA is connected",
        "Show the target, whether NVDA is connected, how to connect it, and the\n"
        "browse/focus mode (unknown until NVDA first switches).",
    ),
    "speech": (
        "print what NVDA said since the mark",
        """\
Print what NVDA said, one numbered line per utterance, plus mode changes.
By default, lines after the last asc mark.

examples:
  asc speech
  asc speech --since 0       everything still kept
  asc speech --since 41      lines after 41""",
    ),
    "wait": (
        "wait until NVDA says something matching REGEX",
        """\
Return as soon as an utterance after the mark (or --since) matches REGEX
(Python syntax, searched anywhere in the line; use (?i) to ignore case).
Exits 1 with what NVDA did say if nothing matched in time.

examples:
  asc wait "OK button"
  asc wait "(?i)saved" --timeout 10000""",
    ),
    "mark": ("remember the current end of the speech log", "Print and set the mark."),
    "clear": ("drop all kept speech", "Drop all kept speech. Line numbers keep counting."),
    "press": (
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
    ),
    "type": (
        "type text and print what NVDA said",
        """\
Type TEXT one character at a time, using this computer's keyboard layout.
Every character is checked first; if any can't be typed, nothing is (exit 2).
NVDA gets one --settle quiet period at the end. Watch mode refuses (exit 4).

examples:
  asc type "hello world"
  asc type "x@example.com" --settle 800""",
    ),
    "keys": (
        "look up NVDA commands in NVDA's own reference",
        """\
Print commands from NVDA's Commands Quick Reference whose name, keys,
description or section contain every SEARCH word, one per line:
KEY  NAME: DESCRIPTION. Needs no running asc. The reference comes from the
NVDA the target uses: for desktop mode the portable copy, otherwise your own
NVDA (the running nvda.exe, the uninstall registry entry, Program Files).
In browse mode, single letters jump to elements; add Shift to go back.

examples:
  asc keys heading
  asc keys report title
  asc keys --layout laptop current line""",
    ),
    "launch": (
        "start an app and wait until NVDA speaks",
        """\
Start CMD and wait until NVDA says something matching --title (a regex), or
anything at all if --title is left out. Prints what NVDA said.
Default timeout 30000 ms.

examples:
  asc launch "notepad.exe" --title Notepad
  asc launch "py -m myapp" --title "My App" --timeout 60000""",
    ),
    "get-nvda": (
        "make the portable NVDA that desktop mode launches",
        """\
Download NVDA from NV Access and make a portable copy for desktop mode, in
%LOCALAPPDATA%\\agent-speech-check\\nvda\\portable. Refuses (exit 4) while any
NVDA runs, because the NVDA installer would restart it.

examples:
  asc get-nvda
  asc get-nvda --installer nvda_2026.2.exe""",
    ),
    "sandbox-setup": (
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
    ),
}


def _add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", metavar="NAME", help="target name (default: default)")


def _timeout(parser: argparse.ArgumentParser, default: int = ipc.DEFAULT_TIMEOUT_MS) -> None:
    parser.add_argument(
        "--timeout", type=int, default=default, metavar="MS", help=f"default {default}"
    )


def _settle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--settle",
        type=int,
        default=ipc.DEFAULT_SETTLE_MS,
        metavar="MS",
        help=f"quiet period (default {ipc.DEFAULT_SETTLE_MS})",
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
    parser.add_argument("--layout", choices=("desktop", "laptop"), default="desktop")
    parser.add_argument("--nvda-dir", metavar="DIR", help="read this NVDA's reference")
    parser.add_argument("--lang", default="en", help="reference language (default en)")
    parser.add_argument("--braille", action="store_true", help="include braille displays")


def _launch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("app", metavar="CMD")
    parser.add_argument("--title", metavar="REGEX", help="wait until NVDA says this")
    _timeout(parser, ipc.DEFAULT_LAUNCH_TIMEOUT_MS)


def _get_nvda_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", default=nvda.VERSION, dest="nvda_version")
    parser.add_argument("--installer", metavar="FILE", help="use this installer, no download")


def _sandbox_setup_args(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(mode="sandbox")


ARGS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "start": _start_args,
    "speech": _speech_args,
    "wait": _wait_args,
    "press": _press_args,
    "type": _type_args,
    "keys": _keys_args,
    "launch": _launch_args,
    "get-nvda": _get_nvda_args,
    "sandbox-setup": _sandbox_setup_args,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asc",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"asc {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub: dict[str, argparse.ArgumentParser] = {}
    for name, (summary, details) in HELP.items():
        sub[name] = commands.add_parser(
            name,
            help=summary,
            description=details,
            epilog=EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        _add_target(sub[name])
        if name in ARGS:
            ARGS[name](sub[name])
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


def _target(args: argparse.Namespace) -> Target:
    return resolve(targets_file(), args.target, getattr(args, "mode", None))


def _call(target: Target, cmd: str, raw: dict[str, Any], timeout_ms: int = 0) -> dict[str, Any]:
    request = ipc.parse_request(cmd, raw)
    timeout_s = timeout_ms / 1000 + session.STATUS_TIMEOUT_S
    return ipc.call(target_dir(target.name), target.name, request, timeout_s)


def cmd_start(args: argparse.Namespace) -> int:
    target = _target(args)
    settings = tuple(Setting.parse(text) for text in args.nvda_setting)
    if settings and not (target.mode in ("desktop", "sandbox") and target.launch):
        raise BadSetting(args.nvda_setting[0], "only an NVDA that asc launches takes settings")
    render(Relay("", 1, "", ""), settings)  # clashes between settings, before anything starts
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


def cmd_wait(args: argparse.Namespace) -> int:
    raw = {"pattern": args.regex, "timeout_ms": args.timeout, "since": args.since}
    n, text = _call(_target(args), "wait", raw, args.timeout)["line"]
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
    raw = {
        "gestures": args.gestures,
        "expect": args.expect,
        "settle_ms": args.settle,
        "timeout_ms": args.timeout,
    }
    _show_said(_call(_target(args), "press", raw, args.timeout)["lines"])
    return 0


def cmd_type(args: argparse.Namespace) -> int:
    raw = {"text": args.text, "settle_ms": args.settle, "timeout_ms": args.timeout}
    _show_said(_call(_target(args), "type", raw, args.timeout)["lines"])
    return 0


def _nvda_dirs(args: argparse.Namespace) -> list[tuple[str, Path]]:
    if args.nvda_dir:
        return [("--nvda-dir", Path(args.nvda_dir))]
    target = _target(args)
    if target.mode == "desktop" and target.launch:
        return [("the portable NVDA", nvda.folder_for(target.nvda_dir))]
    return keyref.candidates()


def cmd_keys(args: argparse.Namespace) -> int:
    path = keyref.reference(_nvda_dirs(args), args.lang)
    commands = keyref.parse(path.read_text(encoding="utf-8"))
    found = keyref.search(commands, args.words, args.layout, braille=args.braille)
    _show_lines([c.line(args.layout) for c in found], "(no match)")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    raw = {"command": args.app, "title": args.title, "timeout_ms": args.timeout}
    _show_said(_call(_target(args), "launch", raw, args.timeout + ipc.LAUNCH_START_MS)["lines"])
    return 0


def cmd_sandbox_setup(args: argparse.Namespace) -> int:
    sandbox.require_available()
    target = _target(args)
    program = firewall.daemon_program()
    if firewall.rule_ok(target.port, program):
        print(f"firewall rule already in place for port {target.port}")
        return 0
    print("asking Windows for admin rights to add one firewall rule")
    firewall.create_rule(target.port, program)
    print(f"firewall rule added: {firewall.RULE_NAME}, TCP {target.port}, {firewall.INTERFACE}")
    return 0


def cmd_get_nvda(args: argparse.Namespace) -> int:
    nvda.refuse_if_running()
    folder = nvda.managed_dir()
    if args.installer:
        installer = Path(args.installer)
    else:
        print(f"downloading {nvda.installer_url(args.nvda_version)}")
        installer = nvda.download(args.nvda_version)
    exe = nvda.create_portable(installer, folder)
    print(f"portable NVDA ready: {exe}")
    return 0


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "start": cmd_start,
    "stop": cmd_stop,
    "status": cmd_status,
    "speech": cmd_speech,
    "wait": cmd_wait,
    "mark": cmd_mark,
    "clear": cmd_clear,
    "press": cmd_press,
    "type": cmd_type,
    "keys": cmd_keys,
    "launch": cmd_launch,
    "get-nvda": cmd_get_nvda,
    "sandbox-setup": cmd_sandbox_setup,
}


def run(argv: Sequence[str] | None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["_daemon"] and len(args_list) == 2:  # noqa: PLR2004 - _daemon JSON
        # Hidden: how `asc start` runs the daemon.
        return daemon.run(args_list[1])
    args = build_parser().parse_args(args_list)
    if args.command is None:
        raise NoCommand
    return COMMANDS[args.command](args)


def _utf8_streams() -> None:
    """NVDA speech can hold any character. The Windows default code page can't
    print them all, so results and errors are always UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


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
        print(f"internal error: {type(error).__name__}: {error}", file=sys.stderr)
        print(f"logs: {state_root()}", file=sys.stderr)
        if os.environ.get("ASC_DEBUG") == "1":
            traceback.print_exc()
        return InternalError.exit_code
