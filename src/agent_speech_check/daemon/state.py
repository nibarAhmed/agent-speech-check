"""The daemon's state file: `<target folder>\\state.json` with pid, port, token, start time.

Written atomically once the daemon listens, and removed when it stops. The CLI
proves the file is live by connecting with the token; a stale file is removed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_speech_check.errors import NotConnected, NotRunning
from agent_speech_check.validate import Fields
from agent_speech_check.windows.paths import write_atomic

FILE = "state.json"


@dataclass(frozen=True)
class State:
    pid: int
    port: int
    token: str
    started: float

    def write(self, folder: Path) -> None:
        write_atomic(folder / FILE, json.dumps(asdict(self)))


def read(folder: Path, target: str) -> State:
    path = folder / FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotRunning(target) from None
    except ValueError:
        # Not written by a live daemon (writes are atomic): stale.
        remove(folder)
        raise NotRunning(target) from None
    f = Fields(raw, str(path), NotConnected, ("pid", "port", "token", "started"))
    return State(
        f.number("pid", low=1, high=2**32),
        f.number("port", low=1, high=65535),
        f.text("token", max_len=200),
        f.real("started"),
    )


def remove(folder: Path) -> None:
    (folder / FILE).unlink(missing_ok=True)


def exists(folder: Path) -> bool:
    return (folder / FILE).exists()
