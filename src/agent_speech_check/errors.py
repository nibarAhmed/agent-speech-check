"""Every error `asc` raises, and the exit code each one maps to.

Exit codes are decided here and nowhere else. Code inside `asc` raises a
subclass of `AscError`; `cli.main` turns it into an exit code, and the daemon's
request handler turns it into an IPC error response carrying `kind`.

Messages are built inside the exception classes from structured fields, so call
sites never format strings. Concrete errors subclass one of the six categories
below and override `__init__` to take their own fields.
"""

from __future__ import annotations

from typing import ClassVar


class AscError(Exception):
    """Base class. Never raised directly; raise a category or a concrete subclass."""

    exit_code: ClassVar[int]
    kind: ClassVar[str]

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ExpectationFailed(AscError):
    """A `wait` or `--expect` timed out. The message says what was actually said."""

    exit_code = 1
    kind = "expectation"


class UsageError(AscError):
    """Bad argument, bad `targets.toml` or bad `--nvda-setting`."""

    exit_code = 2
    kind = "usage"


class NotConnected(AscError):
    """No daemon, no target, NVDA not connected, or NVDA not found."""

    exit_code = 3
    kind = "not_connected"


class Refused(AscError):
    """Keys in watch mode, `nvda.exe` already running, or daemon already running."""

    exit_code = 4
    kind = "refused"


class ProtocolError(AscError):
    """Malformed or invalid wire data, or an error message from the server."""

    exit_code = 5
    kind = "protocol"


class InternalError(AscError):
    """An invariant broke. This is a bug in `asc`."""

    exit_code = 6
    kind = "internal"


CATEGORIES: tuple[type[AscError], ...] = (
    ExpectationFailed,
    UsageError,
    NotConnected,
    Refused,
    ProtocolError,
    InternalError,
)

EXIT_INTERRUPTED = 130


def from_kind(kind: str, message: str, hint: str | None = None) -> AscError:
    """Rebuild an error from an IPC response, so the CLI exits with the same code.

    An unknown `kind` means the daemon and CLI disagree, which is itself a bug.
    """
    for category in CATEGORIES:
        if category.kind == kind:
            return category(message, hint)
    return InternalError(f"daemon sent unknown error kind {kind!r}: {message}", hint)
