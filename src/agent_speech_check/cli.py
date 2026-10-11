"""The `asc` command line. The only module that prints.

`main` is the one place where errors become exit codes. Results go to stdout,
errors to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Sequence

from agent_speech_check import __version__
from agent_speech_check.errors import EXIT_INTERRUPTED, AscError, InternalError, UsageError
from agent_speech_check.paths import log_dir

DESCRIPTION = """\
Agent Speech Check: use NVDA the way a blind user does. Press keys, read what
NVDA says as text, and check it.

Commands arrive in milestones M1 to M4; see docs/cli.md.
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


class NoCommand(UsageError):
    def __init__(self) -> None:
        super().__init__("no command given", hint="run asc --help")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asc",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"asc {__version__}")
    return parser


def run(argv: Sequence[str] | None) -> int:
    build_parser().parse_args(argv)
    # M0 has no commands yet; M1 adds subparsers and dispatch here.
    raise NoCommand


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
        print(f"logs: {log_dir()}", file=sys.stderr)
        if os.environ.get("ASC_DEBUG") == "1":
            traceback.print_exc()
        return InternalError.exit_code
