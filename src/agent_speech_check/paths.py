"""Where `asc` keeps its state and logs: `%LOCALAPPDATA%\\agent-speech-check`."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "agent-speech-check"


def state_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_DIR_NAME
