"""The `asc` command line. The only module that prints.

`main` is the one place where errors become exit codes. Results go to stdout,
errors to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from typing import Any

from agent_speech_check import __version__, daemon, ipc, session
from agent_speech_check.errors import EXIT_INTERRUPTED, AscError, InternalError, NoCommand
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
}


def _add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", metavar="NAME", help="target name (default: default)")


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
    sub["start"].add_argument("--mode", choices=MODES, help="mode for an unlisted target")
    sub["speech"].add_argument("--since", type=int, metavar="N", help="lines after N")
    sub["wait"].add_argument("regex", metavar="REGEX")
    sub["wait"].add_argument("--timeout", type=int, default=ipc.DEFAULT_TIMEOUT_MS, metavar="MS")
    sub["wait"].add_argument("--since", type=int, metavar="N", help="lines after N")
    press = sub["press"]
    press.add_argument("gestures", nargs="+", metavar="KEY")
    press.add_argument("--expect", metavar="REGEX", help="exit 1 unless the speech matches")
    press.add_argument(
        "--settle",
        type=int,
        default=ipc.DEFAULT_SETTLE_MS,
        metavar="MS",
        help=f"quiet period per gesture (default {ipc.DEFAULT_SETTLE_MS})",
    )
    press.add_argument("--timeout", type=int, default=ipc.DEFAULT_TIMEOUT_MS, metavar="MS")
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
    if status["last_error"]:
        print(f"last error: {status['last_error']}")


def _show_said(lines: list[str]) -> None:
    print("\n".join(lines) if lines else "(nothing said)")


# ---------------------------------------------------------------- commands


def _target(args: argparse.Namespace) -> Target:
    return resolve(targets_file(), args.target, getattr(args, "mode", None))


def _call(target: Target, cmd: str, raw: dict[str, Any], timeout_ms: int = 0) -> dict[str, Any]:
    request = ipc.parse_request(cmd, raw)
    timeout_s = timeout_ms / 1000 + session.STATUS_TIMEOUT_S
    return ipc.call(target_dir(target.name), target.name, request, timeout_s)


def cmd_start(args: argparse.Namespace) -> int:
    target = _target(args)
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


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "start": cmd_start,
    "stop": cmd_stop,
    "status": cmd_status,
    "speech": cmd_speech,
    "wait": cmd_wait,
    "mark": cmd_mark,
    "clear": cmd_clear,
    "press": cmd_press,
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


def main(argv: Sequence[str] | None = None) -> int:
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
