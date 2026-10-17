"""Running PowerShell scripts safely: single quoting and base64 `-EncodedCommand`."""

from __future__ import annotations

import base64
import subprocess

from agent_speech_check.errors import PowerShellTimedOut

TIMEOUT_S = 60


def quote(text: str) -> str:
    """A PowerShell single-quoted string: only ' needs doubling."""
    return "'" + text.replace("'", "''") + "'"


def encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def command_line(script: str) -> str:
    """One command line that runs `script`, which travels base64-encoded, so no
    quote in it can break out."""
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded(script)}"


def run(script: str, *, timeout_s: float = TIMEOUT_S) -> str:
    """Run `script` and return what it printed."""
    try:
        return subprocess.run(  # noqa: S603 - fixed program, our own script
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(script)],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_s,
        ).stdout
    except subprocess.TimeoutExpired:
        raise PowerShellTimedOut(timeout_s) from None
