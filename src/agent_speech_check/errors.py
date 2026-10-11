"""Every error `asc` raises, and the exit code each one maps to.

Exit codes are decided here and nowhere else. Code inside `asc` raises a
subclass of `AscError`; `cli.main` turns it into an exit code, and the daemon's
request handler turns it into an IPC error response carrying `kind`.

Messages are built inside the exception classes from structured fields, so call
sites never format strings. Concrete errors subclass one of the six categories
below and override `__init__` to take their own fields.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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


class NoCommand(UsageError):
    """`asc` was run without a command."""

    def __init__(self) -> None:
        super().__init__("no command given", hint="run asc --help")


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


# ---------------------------------------------------------------- concrete errors


_SHOW_MAX = 80


def _show(value: object) -> str:
    """A short, unambiguous rendering of a bad value for an error message."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= _SHOW_MAX else text[: _SHOW_MAX - 3] + "..."


def bad_field(
    category: type[AscError], where: str, expected: str, got: object, *, quote: bool = True
) -> AscError:
    """A field failed validation. `category` picks the exit code (usage, protocol...)."""
    shown = _show(got) if quote else str(got)
    if got is None and quote:
        shown = "nothing"
    return category(f"{where}: expected {expected}, got {shown}")


def said_lines(said: Sequence[str]) -> str:
    """What NVDA said, as `asc` prints it, for messages that report it."""
    if not said:
        return "(nothing said)"
    return "\n".join(said_line(text) for text in said)


def said_line(text: str) -> str:
    """One utterance as `asc` prints it. Lives here so error messages match output."""
    return f"said: {json.dumps(text, ensure_ascii=False)}"


class BadRegex(UsageError):
    def __init__(self, pattern: str, reason: str, position: int | None) -> None:
        at = f" at position {position}" if position is not None else ""
        super().__init__(f"bad regex {_show(pattern)}: {reason}{at}")


class UnknownKey(UsageError):
    def __init__(self, name: str, suggestions: Sequence[str]) -> None:
        hint = f"did you mean {', '.join(suggestions)}?" if suggestions else "see asc keys"
        super().__init__(f"unknown key {_show(name)}", hint=hint)


class BadGesture(UsageError):
    def __init__(self, gesture: str, reason: str) -> None:
        super().__init__(f"bad gesture {_show(gesture)}: {reason}")


class UntypableText(UsageError):
    def __init__(self, chars: Sequence[str]) -> None:
        shown = " ".join(_show(c) for c in chars)
        super().__init__(
            f"cannot type these characters with the current keyboard layout: {shown}",
            hint="nothing was sent; use asc press for special keys",
        )


class ModeMismatch(UsageError):
    def __init__(self, target: str, configured: str, given: str) -> None:
        super().__init__(
            f"target {_show(target)} is {configured} mode in targets.toml, not {given}",
            hint="leave out --mode, or use another --target",
        )


class BadSetting(UsageError):
    def __init__(self, setting: str, reason: str) -> None:
        super().__init__(f"--nvda-setting {_show(setting)}: {reason}")


class ModeNotBuilt(UsageError):
    def __init__(self, mode: str) -> None:
        super().__init__(
            f"{mode} mode is not built yet",
            hint="use --mode watch, or a desktop target with launch = false",
        )


class TargetsFileError(UsageError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")


class NotRunning(NotConnected):
    def __init__(self, target: str) -> None:
        super().__init__(
            f"asc is not running for target {_show(target)} (not recording)",
            hint=f"asc start --target {target}",
        )


class NvdaNotConnected(NotConnected):
    def __init__(self, target: str) -> None:
        super().__init__(
            f"NVDA is not connected to target {_show(target)}",
            hint="asc status shows how NVDA should connect",
        )


class DaemonStartFailed(NotConnected):
    def __init__(self, exit_code: int | None, waited_s: float, log_tail: str) -> None:
        reason = (
            f"not ready after {waited_s:g} s"
            if exit_code is None
            else f"it exited with code {exit_code}"
        )
        tail = f"\nlast daemon log lines:\n{log_tail}" if log_tail else ""
        super().__init__(f"the asc daemon did not start: {reason}{tail}")


class DaemonVersionMismatch(NotConnected):
    def __init__(self, theirs: object) -> None:
        super().__init__(
            f"the running asc daemon speaks IPC version {_show(theirs)}, not {IPC_VERSION}",
            hint="asc stop, then asc start",
        )


class DaemonUnreachable(NotConnected):
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"cannot talk to the asc daemon: {reason}", hint="asc stop, then asc start"
        )


class DaemonNoReply(DaemonUnreachable):
    def __init__(self) -> None:
        super().__init__("no reply in time")


class DaemonHungUp(DaemonUnreachable):
    def __init__(self) -> None:
        super().__init__("it closed the connection without a reply")


class NvdaNotFound(NotConnected):
    def __init__(self, looked: Sequence[str], hint: str = "pass --nvda-dir") -> None:
        where = "; ".join(looked) if looked else "nowhere"
        super().__init__(f"NVDA not found. Looked in: {where}", hint=hint)


class PortableFailed(NotConnected):
    def __init__(self, folder: str) -> None:
        super().__init__(
            f"the NVDA installer made no portable copy in {folder}",
            hint="no other NVDA may run meanwhile; try asc get-nvda again",
        )


class LaunchFailed(UsageError):
    def __init__(self, command: str, reason: str) -> None:
        super().__init__(f"cannot start {_show(command)}: {reason}")


class LaunchRefused(Refused):
    def __init__(self, mode: str) -> None:
        super().__init__(
            f"asc launch is not available in {mode} mode yet",
            hint="start the app yourself, then asc wait for its title",
        )


class SandboxFailed(NotConnected):
    def __init__(self, what: str, detail: str) -> None:
        super().__init__(f"wsb {what} failed: {detail or 'no detail'}")


class NoDefaultSwitch(NotConnected):
    def __init__(self, interface: str) -> None:
        super().__init__(
            f"no IPv4 address on {_show(interface)}, so the sandbox can't reach asc",
            hint="start any Windows Sandbox once, which creates the Default Switch",
        )


class FirewallRuleMissing(NotConnected):
    def __init__(self) -> None:
        super().__init__(
            "the firewall rule that lets the sandbox reach asc is missing",
            hint="asc sandbox-setup (asks for admin rights once)",
        )


class FirewallFailed(NotConnected):
    def __init__(self, rule: str) -> None:
        super().__init__(
            f"the firewall rule {_show(rule)} was not created",
            hint="approve the admin prompt, or run asc sandbox-setup again",
        )


class NvdaDidNotConnect(NotConnected):
    def __init__(self, seconds: float, log_tail: str) -> None:
        tail = f"; last daemon log lines:{chr(10)}{log_tail}" if log_tail else ""
        super().__init__(f"NVDA did not connect within {seconds:g} s, so asc stopped{tail}")


class SandboxBusy(Refused):
    def __init__(self) -> None:
        super().__init__(
            "a Windows Sandbox is already running, and Windows allows only one",
            hint="close it, or asc stop the target that started it",
        )


class SandboxNeedsPro(Refused):
    def __init__(self) -> None:
        super().__init__(
            "sandbox mode is unavailable: Windows Sandbox needs Windows Pro, "
            "Enterprise or Education",
            hint="use watch or desktop mode",
        )


class WsbMissing(Refused):
    def __init__(self) -> None:
        super().__init__(
            "sandbox mode is unavailable: the wsb command is missing",
            hint="turn on the Windows Sandbox feature, then sign out and in",
        )


class KeysRefused(Refused):
    def __init__(self, target: str) -> None:
        super().__init__(
            f"target {_show(target)} is in watch mode, which never sends keys",
            hint="use a desktop or sandbox target to send keys",
        )


class AlreadyRunning(Refused):
    def __init__(self, target: str) -> None:
        super().__init__(
            f"asc is already running for target {_show(target)}",
            hint=f"asc stop --target {target}",
        )


class NvdaAlreadyRunning(Refused):
    def __init__(self) -> None:
        super().__init__(
            "nvda.exe is already running; starting another NVDA would restart it",
            hint="use watch or sandbox mode, which leave your NVDA alone",
        )


class KeysBusy(ExpectationFailed):
    def __init__(self, timeout_ms: int) -> None:
        super().__init__(
            f"another asc command kept the keyboard for the whole {timeout_ms} ms timeout",
            hint="retry, or raise --timeout",
        )


class NoMatch(ExpectationFailed):
    def __init__(self, pattern: str, timeout_ms: int, said: Sequence[str]) -> None:
        super().__init__(
            f"no speech matched {_show(pattern)} within {timeout_ms} ms. "
            f"NVDA said:\n{said_lines(said)}"
        )


class LineTooLong(ProtocolError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"NVDA sent a line longer than {limit} bytes")


class MalformedLine(ProtocolError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"NVDA sent a line that is not a JSON object: {reason}")


class ServerSentError(ProtocolError):
    def __init__(self, text: str) -> None:
        super().__init__(f"NVDA reported an error: {_show(text)}")


class NoJoin(ProtocolError):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"the connection sent no join within {seconds:g} s")


CATEGORIES: tuple[type[AscError], ...] = (
    ExpectationFailed,
    UsageError,
    NotConnected,
    Refused,
    ProtocolError,
    InternalError,
)

_BY_KIND = {category.kind: category for category in CATEGORIES}

EXIT_INTERRUPTED = 130

# The IPC version lives here so `DaemonVersionMismatch` can name it.
IPC_VERSION = 1


def from_kind(kind: str, message: str, hint: str | None = None) -> AscError:
    """Rebuild an error from an IPC response, so the CLI exits with the same code.

    An unknown `kind` means the daemon and CLI disagree, which is itself a bug.
    """
    category = _BY_KIND.get(kind)
    if category is not None:
        return category(message, hint)
    return InternalError(f"daemon sent unknown error kind {kind!r}: {message}", hint)
