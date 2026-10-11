"""Targets: named NVDA setups, optionally listed in `targets.toml`.

`targets.toml` sits in the state folder (`%LOCALAPPDATA%\\agent-speech-check`):

    default = "app"

    [targets.app]
    mode = "desktop"    # watch, desktop, sandbox or shared
    port = 16837        # relay port; watch mode defaults to 6837, others pick a free one
    keys = true         # may send keys; never true in watch mode
    launch = true       # desktop mode: false if NVDA is started elsewhere and connects itself

A target that isn't listed gets the defaults for `--mode` (watch if left out).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_speech_check.errors import ModeMismatch, TargetsFileError, UsageError, bad_field
from agent_speech_check.validate import Fields

MODES = ("watch", "desktop", "sandbox", "shared")
DEFAULT_TARGET = "default"
WATCH_PORT = 6837
_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
_KEYS = ("mode", "port", "keys", "launch")


@dataclass(frozen=True)
class Target:
    name: str
    mode: str
    port: int  # 0: pick a free port
    keys: bool
    launch: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Target:
        """The daemon re-checks the target it is given, since it is an edge too."""
        try:
            raw = json.loads(text)
        except ValueError as error:
            raise bad_field(UsageError, "daemon target", "JSON", str(error)) from None
        f = Fields(raw, "daemon target", UsageError, ("name", *_KEYS))
        target = Target(
            check_name(f.text("name")),
            f.choice("mode", MODES),
            f.number("port", low=0, high=65535),
            keys=f.flag("keys"),
            launch=f.flag("launch"),
        )
        return _checked(target, "daemon target")


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise bad_field(UsageError, "target name", "1 to 32 letters, digits, - or _", name)
    return name


def _checked(target: Target, where: str) -> Target:
    if target.mode == "watch" and target.keys:
        raise bad_field(UsageError, f"{where} keys", "false in watch mode", target.keys)
    return target


def defaults(name: str, mode: str) -> Target:
    return Target(
        name,
        mode,
        WATCH_PORT if mode == "watch" else 0,
        keys=mode != "watch",
        launch=mode == "desktop",
    )


def _load(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as error:
        raise TargetsFileError(str(path), str(error)) from None


def _from_table(name: str, raw: object, path: Path) -> Target:
    where = f"{path.name}: [targets.{name}]"
    f = Fields(raw, where, UsageError, _KEYS)
    mode = f.choice("mode", MODES)
    base = defaults(name, mode)
    port = f.number("port", low=1, high=65535) if f.has("port") else base.port
    keys = f.flag("keys") if f.has("keys") else base.keys
    launch = f.flag("launch") if f.has("launch") else base.launch
    return _checked(Target(name, mode, port, keys, launch), where)


def resolve(path: Path, name: str | None, mode: str | None) -> Target:
    """The target to use: from `targets.toml` if listed, else defaults for `mode`."""
    data = _load(path)
    top = Fields(data, path.name, UsageError, ("default", "targets"))
    tables = Fields(data.get("targets", {}), f"{path.name}: [targets]", UsageError)
    chosen = check_name(name or top.opt_text("default", max_len=32) or DEFAULT_TARGET)
    if chosen in tables.raw:
        target = _from_table(chosen, tables.raw[chosen], path)
        if mode is not None and mode != target.mode:
            raise ModeMismatch(chosen, target.mode, mode)
        return target
    return defaults(chosen, mode or "watch")
