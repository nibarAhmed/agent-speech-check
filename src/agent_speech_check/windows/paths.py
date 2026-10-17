"""Where `asc` keeps its state and logs: `%LOCALAPPDATA%\\agent-speech-check`."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from agent_speech_check.errors import FileUnusable

APP_DIR_NAME = "agent-speech-check"


def state_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME


def config_file() -> Path:
    return state_root() / "config.toml"


def mode_dir(mode: str) -> Path:
    """One folder per mode: state file, daemon log, relay cert and credentials."""
    return state_root() / mode


def write_atomic(path: Path, data: str | bytes) -> None:
    """Write `path` in one step: readers see the old file or the new one, never half.

    Text is written as UTF-8 with Unix line ends.
    """
    try:
        _write_atomic(path, data)
    except OSError as error:
        # Names `path`, not the temporary file the error may name.
        raise FileUnusable.from_os(path, error) from None


def _write_atomic(path: Path, data: str | bytes) -> None:
    # Its own temporary name, so two writers at once never share one.
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    tmp = Path(name)
    try:
        if isinstance(data, bytes):
            with os.fdopen(handle, "wb") as file:
                file.write(data)
        else:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
                file.write(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
