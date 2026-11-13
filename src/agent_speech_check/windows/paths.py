"""Where `asc` keeps its state and logs: `%LOCALAPPDATA%\\agent-speech-check`.

A named instance (`ASC_INSTANCE`, or `--instance`) is a whole separate home in
`instances\\<name>`: its own config, daemon and relay certificate.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from agent_speech_check.errors import BadInstance, FileUnusable

APP_DIR_NAME = "agent-speech-check"
INSTANCE_VAR = "ASC_INSTANCE"
INSTANCES_DIR = "instances"
_DEVICES = re.compile(r"con|prn|aux|nul|com\d|lpt\d")


def base_root() -> Path:
    """The default instance's home. It also keeps the NVDA every instance shares."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME


def check_instance(name: str) -> str:
    # It names a folder, so neither ..\ nor a Windows device name may get in.
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name) or _DEVICES.fullmatch(name):
        raise BadInstance(name)
    return name


def instance() -> str | None:
    """The named instance this process works on, or None for the default one."""
    name = os.environ.get(INSTANCE_VAR)
    return check_instance(name) if name else None


def instance_flag() -> str:
    """` --instance NAME`, for a hint that must reach this instance, else nothing."""
    name = instance()
    return f" --instance {name}" if name else ""


def every_root() -> list[Path]:
    """The default instance's home, then each named one's."""
    named = sorted(p for p in (base_root() / INSTANCES_DIR).glob("*") if p.is_dir())
    return [base_root(), *named]


def state_root() -> Path:
    name = instance()
    return base_root() if name is None else base_root() / INSTANCES_DIR / name


def config_file() -> Path:
    return state_root() / "config.toml"


def mode_dir(mode: str) -> Path:
    """One folder per mode: state file and daemon log."""
    return state_root() / mode


def relay_dir(mode: str) -> Path:
    """Where the mode's relay certificate and channel key are kept. Shared mode
    uses watch mode's: both are joined by the user's own NVDA, which then trusts
    one certificate and comes back by itself whichever of the two starts."""
    return mode_dir("watch" if mode == "shared" else mode)


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
