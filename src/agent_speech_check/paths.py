"""Where `asc` keeps its state and logs: `%LOCALAPPDATA%\\agent-speech-check`."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "agent-speech-check"


def state_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME


def targets_file() -> Path:
    return state_root() / "targets.toml"


def target_dir(name: str) -> Path:
    """One folder per target: state file, daemon log, relay cert and credentials."""
    return state_root() / name
