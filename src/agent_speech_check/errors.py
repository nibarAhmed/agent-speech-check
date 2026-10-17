"""Every error `asc` raises, and the exit code each one maps to.

Exit codes are decided here and nowhere else. Code inside `asc` raises a
subclass of `AscError`; `cli.main` turns it into an exit code, and the daemon's
request handler turns it into an IPC error response carrying `kind`.

Messages are built inside the exception classes from structured fields, so call
sites never format strings. Concrete errors subclass one of the categories
below and override `__init__` to take their own fields.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from os import PathLike
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
    """A `wait` or `--expect` timed out. Report what the app said; the message has it."""

    exit_code = 1
    kind = "expectation"


class UsageError(AscError):
    """The input is wrong: fix the arguments, `config.toml` or `--nvda-setting`."""

    exit_code = 2
    kind = "usage"


class NoCommand(UsageError):
    """`asc` was run without a command."""

    def __init__(self) -> None:
        super().__init__("no command given", hint="asc --help")


class NotConnected(AscError):
    """No daemon, or NVDA not connected. Retrying or `asc start` can fix it.

    Only for failures a retry usually fixes; if the cause is unknown, pick one
    that stops the caller.
    """

    exit_code = 3
    kind = "not_connected"


class Refused(AscError):
    """`asc` won't do it as asked (policy, or something busy); the hint says what to do."""

    exit_code = 4
    kind = "refused"


class ProtocolError(AscError):
    """NVDA sent something `asc` can't accept. Retrying rarely helps; report it."""

    exit_code = 5
    kind = "protocol"


class InternalError(AscError):
    """An invariant broke. This is a bug in `asc`."""

    exit_code = 6
    kind = "internal"


class SetupNeeded(AscError):
    """Something must be installed or set up first; retrying won't help."""

    exit_code = 7
    kind = "setup"


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


class BadArguments(UsageError):
    def __init__(self, reason: str, prog: str) -> None:
        super().__init__(reason, hint=f"{prog} --help")


class UnrecognizedArguments(BadArguments):
    def __init__(self, extra: Sequence[str], prog: str) -> None:
        super().__init__(f"unrecognized arguments: {' '.join(extra)}", prog)


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


class BadSetting(UsageError):
    def __init__(self, setting: str, reason: str, where: str = "--nvda-setting") -> None:
        super().__init__(f"{where} {_show(setting)}: {reason}")


class UserConfigUnreadable(UsageError):
    def __init__(self, where: str, line: int | None) -> None:
        at = "" if line is None else f" line {line}"
        super().__init__(
            f"cannot read your NVDA config: {where}{at}",
            hint="fix that line, or point nvda_dir at an NVDA with a fresh config",
        )


class ConfigFileError(UsageError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")


class UnknownPreference(UsageError):
    def __init__(self, name: str, suggestions: Sequence[str]) -> None:
        hint = f"did you mean {', '.join(suggestions)}?" if suggestions else "asc config list"
        super().__init__(f"unknown setting {_show(name)}", hint=hint)


class BadPreference(UsageError):
    def __init__(self, name: str, reason: str, hint: str | None = None) -> None:
        super().__init__(f"setting {_show(name)}: {reason}", hint=hint)


class BadInFile(UsageError):
    """A value from config.toml failed a parser that doesn't know where it came from."""

    def __init__(self, where: str, reason: str, hint: str | None = None) -> None:
        super().__init__(f"{where}: {reason}", hint=hint)


class NeedsTerminal(UsageError):
    def __init__(self) -> None:
        super().__init__(
            "asc setup asks questions, so it needs a terminal",
            hint="use asc config set to change one setting",
        )


class NotRunning(NotConnected):
    def __init__(self) -> None:
        super().__init__("asc is not running (not recording)", hint="asc start")


class NvdaNotConnected(NotConnected):
    def __init__(self, waited_ms: int | None = None, last_error: str | None = None) -> None:
        waited = "" if waited_ms is None else f" after waiting {waited_ms} ms"
        why = f"; last error: {last_error}" if last_error else ""
        super().__init__(
            f"NVDA is not connected{waited}{why}",
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


class PowerShellTimedOut(NotConnected):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"PowerShell gave no answer within {seconds:g} s", hint="try again")


class NvdaNotFound(SetupNeeded):
    def __init__(self, looked: Sequence[str], hint: str) -> None:
        where = "; ".join(looked) if looked else "nowhere"
        super().__init__(f"NVDA not found. Looked in: {where}", hint=hint)


class NvdaTooOld(SetupNeeded):
    def __init__(self, folder: str, version: str, minimum: str) -> None:
        super().__init__(
            f"the NVDA in {folder} is version {version}; asc needs {minimum} or later",
            hint="asc get-nvda",
        )


class PortableFailed(SetupNeeded):
    def __init__(self, folder: str) -> None:
        super().__init__(
            f"the NVDA installer made no portable copy in {folder}",
            hint="check the installer, or let asc get-nvda download it again",
        )


class InstallerTimedOut(SetupNeeded):
    def __init__(self, seconds: float, left: Sequence[int] = ()) -> None:
        message = f"the NVDA installer did not finish within {seconds:g} s"
        if left:
            pids = ", ".join(map(str, left))
            message += f", and asc could not end process {pids}"
            hint = "end those in Task Manager, then asc get-nvda"
        else:
            message += ", so asc ended it"
            hint = "check the installer, or let asc get-nvda download it again"
        super().__init__(message, hint=hint)


class DownloadFailed(NotConnected):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(
            f"cannot download NVDA from {url}: {reason}",
            hint="check the network, or pass --installer to asc get-nvda",
        )


class LaunchFailed(UsageError):
    def __init__(self, command: str, reason: str) -> None:
        super().__init__(f"cannot start {_show(command)}: {reason}")


class SandboxFailed(NotConnected):
    """A sandbox step that a retry can fix: a timeout, or a sandbox still starting or stopping."""

    def __init__(self, what: str, detail: str) -> None:
        super().__init__(f"wsb {what} failed: {detail or 'no detail'}")


class SandboxBroken(SetupNeeded):
    """wsb reported an error, or the sandbox's NVDA script failed: the cause is unknown."""

    def __init__(self, what: str, detail: str) -> None:
        super().__init__(
            f"wsb {what} failed: {detail or 'no detail'}",
            hint="check that Windows Sandbox starts on its own",
        )


class RelayFileBroken(SetupNeeded):
    """A relay file, which asc wrote itself, is broken; no argument can fix it."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            detail,
            hint="delete it; asc makes a new one, and NVDA needs the new key once",
        )


class RelayCertBroken(RelayFileBroken):
    def __init__(self, path: str) -> None:
        super().__init__(f"{path} is not a PEM certificate")


class FileUnusable(SetupNeeded):
    """The disk refused a file or its folder (locked, a folder, a full disk)."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"cannot use {path}: {reason}",
            hint="check that path's permissions and free space",
        )

    @classmethod
    def from_os(cls, path: str | PathLike[str], error: OSError) -> FileUnusable:
        return cls(str(path), error.strerror or str(error))


class NoDefaultSwitch(SetupNeeded):
    def __init__(self, interface: str) -> None:
        super().__init__(
            f"no IPv4 address on {_show(interface)}, so the sandbox can't reach asc",
            hint="start any Windows Sandbox once, which creates the Default Switch",
        )


class FirewallRuleMissing(SetupNeeded):
    def __init__(self) -> None:
        super().__init__(
            "the firewall rule that lets the sandbox reach asc is missing",
            hint="asc sandbox-setup (asks for admin rights once)",
        )


class FirewallFailed(SetupNeeded):
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
            hint="close it, or asc stop if asc started it",
        )


class SharedNeedsConsent(Refused):
    def __init__(self) -> None:
        super().__init__(
            "shared mode sends keys into your own Windows session through your own NVDA",
            hint="if that is what you want, add --i-understand; a panic hotkey stops asc",
        )


class PanicKeyTaken(Refused):
    def __init__(self, key: str) -> None:
        super().__init__(
            f"cannot register the panic hotkey {key}: another program owns it",
            hint="asc never sends keys to this desktop without one; close that program, "
            "or pick another key with asc config set panic_key",
        )


class SandboxNeedsPro(SetupNeeded):
    def __init__(self) -> None:
        super().__init__(
            "sandbox mode is unavailable: Windows Sandbox needs Windows Pro, "
            "Enterprise or Education",
            hint="use watch or desktop mode",
        )


class WsbMissing(SetupNeeded):
    def __init__(self) -> None:
        super().__init__(
            "sandbox mode is unavailable: the wsb command is missing",
            hint="turn on the Windows Sandbox feature, then sign out and in",
        )


class KeysRefused(Refused):
    def __init__(self) -> None:
        super().__init__(
            "asc is in watch mode, which never sends keys",
            hint="asc stop, then asc start --mode desktop or sandbox",
        )


class AlreadyRunning(Refused):
    def __init__(self) -> None:
        super().__init__("asc is already running", hint="asc stop, then try again")


class DaemonNotAnswering(Refused):
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"asc is running but doesn't answer: {reason}",
            hint="asc stop, then asc start",
        )


class DaemonUnstoppable(Refused):
    def __init__(self, pids: Sequence[int]) -> None:
        self.pids = list(pids)
        listed = ", ".join(str(pid) for pid in self.pids)
        if len(self.pids) == 1:
            message, them = f"cannot end the asc daemon (pid {listed})", "it"
        else:
            message, them = f"cannot end the asc daemons (pid {listed})", "them"
        super().__init__(message, hint=f"end {them} in Task Manager, then try again")


class AscStillRunning(Refused):
    def __init__(self, pids: Sequence[int]) -> None:
        super().__init__(
            f"asc is still running (pid {', '.join(str(pid) for pid in pids)})",
            hint="close it, or end it in Task Manager, then try again",
        )


class NvdaAlreadyRunning(Refused):
    def __init__(self) -> None:
        super().__init__(
            "nvda.exe is already running; starting another NVDA would restart it",
            hint="use watch or sandbox mode, which leave your NVDA alone",
        )


class KeysBusy(Refused):
    def __init__(self, timeout_ms: int) -> None:
        super().__init__(
            f"another asc command kept the keyboard for the whole {timeout_ms} ms timeout",
            hint="retry, or raise --timeout",
        )


class NoMatch(ExpectationFailed):
    def __init__(
        self,
        pattern: str,
        timeout_ms: int,
        said: Sequence[str],
        windows: Sequence[str] | None = None,
    ) -> None:
        """`windows`: titles of the windows that opened during a launch, when known."""
        message = f"no speech matched {_show(pattern)} within {timeout_ms} ms. "
        message += f"NVDA said:\n{said_lines(said)}"
        if windows is not None:
            shown = ", ".join(_show(title) for title in windows) or "(none)"
            message += f"\nNew windows: {shown}"
        super().__init__(message)


class LineTooLong(ProtocolError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"NVDA sent a line longer than {limit} bytes")


class MalformedLine(ProtocolError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"NVDA sent a line that is not a JSON object: {reason}")


class ServerSentError(ProtocolError):
    def __init__(self, text: str) -> None:
        super().__init__(f"NVDA reported an error: {_show(text)}")


class JoinRefused(ProtocolError):
    def __init__(self) -> None:
        super().__init__("refused a join with the wrong channel key or role")


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
    SetupNeeded,
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
