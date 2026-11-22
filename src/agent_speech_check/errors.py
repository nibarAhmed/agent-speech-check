"""Every error `asc` raises, and the exit code each one maps to.

Exit codes are decided here and nowhere else. Code inside `asc` raises a
category or concrete subclass of `AscError`; `cli.main` turns it into an exit
code, and the daemon's request handler turns it into an IPC error response
carrying `kind`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from os import PathLike
from typing import Any, ClassVar, Literal


class AscError(Exception):
    """Base class. Never raised directly; raise a category or a concrete subclass."""

    exit_code: ClassVar[int]
    kind: ClassVar[str]

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ExpectationFailed(AscError):
    """A `wait`, `--expect` or `launch` timed out. Report what the app said; the message has it."""

    exit_code = 1
    kind = "expectation"


class UsageError(AscError):
    """The input or invocation is wrong: fix the arguments, `config.toml` or `--nvda-setting`."""

    exit_code = 2
    kind = "usage"


class OperationalError(AscError):
    """Something failed at runtime (not running, disconnected, busy, refused, setup, I/O, protocol).

    The message and hint say what happened and what to do next.
    """

    exit_code = 3
    kind = "operational"


class InternalError(AscError):
    """An invariant broke. This is a bug in `asc`."""

    exit_code = 4
    kind = "internal"


# ---------------------------------------------------------------- helpers


_SHOW_MAX = 80


def show(value: object) -> str:
    """A short, unambiguous rendering of a bad value for an error message."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= _SHOW_MAX else text[: _SHOW_MAX - 3] + "..."


def bad_field(
    category: type[AscError], where: str, expected: str, got: object, *, quote: bool = True
) -> AscError:
    """A field failed validation. `category` picks the exit code (usage, operational...)."""
    shown = show(got) if quote else str(got)
    if got is None and quote:
        shown = "nothing"
    return category(f"{where}: expected {expected}, got {shown}")


def numbered_lines(lines: Sequence[Sequence[Any]], after: int, dropped: int = 0) -> str:
    """`[n, line]` pairs as `asc` prints them, in output and in errors alike.

    With no lines it names `after`, so the caller still has its next --since.
    """
    shown = [f"({dropped} earlier lines dropped)"] if dropped else []
    shown += [f"{n} {line}" for n, line in lines] or [f"(nothing said after {after})"]
    return "\n".join(shown)


# ---------------------------------------------------------------- concrete errors
# Only errors caught by type in `asc`, shared across multiple sites, or polymorphic.


class NoMatch(ExpectationFailed):
    def __init__(
        self,
        pattern: str,
        timeout_ms: int,
        said: str,
        windows: Sequence[str] | None = None,
        reader: Literal["nvda", "orca"] = "nvda",
    ) -> None:
        """`said`: what the screen reader said, from `numbered_lines`, so the agent
        has its next --since. `windows`: titles of the windows that opened during a
        launch, when known."""
        message = f"no speech matched {show(pattern)} within {timeout_ms} ms. "
        name = "Orca" if reader == "orca" else "NVDA"
        message += f"{name} said:\n{said}"
        if windows is not None:
            shown = ", ".join(show(title) for title in windows) or "(none)"
            message += f"\nNew windows: {shown}"
        super().__init__(message)


class UnknownKey(UsageError):
    def __init__(self, name: str, suggestions: Sequence[str]) -> None:
        hint = f"did you mean {', '.join(suggestions)}?" if suggestions else "see asc keys"
        super().__init__(f"unknown key {show(name)}", hint=hint)


class BadGesture(UsageError):
    def __init__(self, gesture: str, reason: str) -> None:
        super().__init__(f"bad gesture {show(gesture)}: {reason}")


class BadSetting(UsageError):
    def __init__(self, setting: str, reason: str, where: str = "--nvda-setting") -> None:
        super().__init__(f"{where} {show(setting)}: {reason}")


class NotRunning(OperationalError):
    """No daemon runs in this state folder (missing/corrupt state file, or refused connection)."""

    def __init__(self) -> None:
        super().__init__("asc is not running (not recording)", hint="asc start")


class DaemonUnreachable(OperationalError):
    """A state file exists, so a daemon may still run, but this CLI cannot talk to it."""

    def __init__(self, message: str, hint: str | None = "asc stop, then asc start") -> None:
        super().__init__(message, hint=hint)


class DaemonUnstoppable(OperationalError):
    def __init__(self, pids: Sequence[int]) -> None:
        self.pids = list(pids)
        listed = ", ".join(str(pid) for pid in self.pids)
        if len(self.pids) == 1:
            message, them = f"cannot end the asc daemon (pid {listed})", "it"
        else:
            message, them = f"cannot end the asc daemons (pid {listed})", "them"
        how = "in Task Manager" if sys.platform == "win32" else "with kill"
        super().__init__(message, hint=f"end {them} {how}, then try again")


class AlreadyRunning(OperationalError):
    def __init__(self, mode: str | None = None) -> None:
        where = f" in {mode} mode" if mode else ""
        super().__init__(
            f"asc is already running{where}",
            hint="asc stop only if you started it",
        )


class SharedNeedsConsent(OperationalError):
    def __init__(self) -> None:
        super().__init__(
            "shared mode sends keys into your own Windows session through your own NVDA",
            hint="if that is what you want, add --i-understand; a panic hotkey stops asc",
        )


class PortInUse(OperationalError):
    def __init__(self, port: int, command: str) -> None:
        super().__init__(
            f"port {port} is in use, maybe by another asc instance",
            hint=f"{command} (a free one)",
        )


class SshMissing(OperationalError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"cannot run ssh: {detail}", hint="install the OpenSSH client")


class LaunchFailed(OperationalError):
    def __init__(self, command: str, reason: str) -> None:
        super().__init__(f"cannot start {show(command)}: {reason}")


class ReaderNotConnected(OperationalError):
    """What each reader's link raises; its subclasses name the reader."""

    what = "the screen reader is not connected"
    next_step = "asc status says why"

    def __init__(self, waited_ms: int | None = None, last_error: str | None = None) -> None:
        waited = "" if waited_ms is None else f" after waiting {waited_ms} ms"
        why = f"; last error: {last_error}" if last_error else ""
        super().__init__(f"{self.what}{waited}{why}", hint=self.next_step)


class NvdaNotConnected(ReaderNotConnected):
    what = "NVDA is not connected"
    next_step = "asc status shows how NVDA should connect"


class OrcaNotConnected(ReaderNotConnected):
    what = "Orca is not running"
    next_step = "asc status says why; asc stop, then asc start"


class ProtocolError(OperationalError):
    """NVDA or Orca's helper sent something `asc` can't accept."""


class DownloadFailed(OperationalError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(
            f"cannot download NVDA from {url}: {reason}",
            hint="check the network, or pass --installer to asc get-nvda",
        )


class PowerShellTimedOut(OperationalError):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"PowerShell gave no answer within {seconds:g} s", hint="try again")


class SandboxError(OperationalError):
    def __init__(self, what: str, detail: str, hint: str | None = None) -> None:
        super().__init__(f"wsb {what} failed: {detail or 'no detail'}", hint=hint)


class FileUnusable(OperationalError):
    """The disk refused a file or its folder (locked, a folder, a full disk)."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"cannot use {path}: {reason}",
            hint="check that path's permissions and free space",
        )

    @classmethod
    def from_os(cls, path: str | PathLike[str], error: OSError) -> FileUnusable:
        return cls(str(path), error.strerror or str(error))


CATEGORIES: tuple[type[AscError], ...] = (
    ExpectationFailed,
    UsageError,
    OperationalError,
    InternalError,
)

_BY_KIND = {category.kind: category for category in CATEGORIES}

EXIT_INTERRUPTED = 130

IPC_VERSION = 4


def from_kind(kind: str, message: str, hint: str | None = None) -> AscError:
    """Rebuild an error from an IPC response, so the CLI exits with the same code.

    An unknown `kind` means the daemon and CLI disagree, which is itself a bug.
    """
    category = _BY_KIND.get(kind)
    if category is not None:
        return category(message, hint)
    return InternalError(f"daemon sent unknown error kind {kind!r}: {message}", hint)
