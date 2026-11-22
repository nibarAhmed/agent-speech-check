"""Running PowerShell scripts safely: single quoting and base64 `-EncodedCommand`."""

from __future__ import annotations

import base64


def quote(text: str) -> str:
    """A PowerShell single-quoted string: only ' needs doubling."""
    return "'" + text.replace("'", "''") + "'"


def encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def command_line(script: str) -> str:
    """One command line that runs `script`, which travels base64-encoded, so no
    quote in it can break out."""
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded(script)}"
