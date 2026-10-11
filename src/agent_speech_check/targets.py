"""Targets: named NVDA setups, optionally listed in `targets.toml`.

`targets.toml` sits in the state folder (`%LOCALAPPDATA%\\agent-speech-check`):

    default = "app"

    [targets.app]
    mode = "desktop"    # watch, desktop, sandbox or shared
    port = 16837        # relay port; watch 6837, sandbox 16837, desktop a free one
    keys = true         # may send keys; never true in watch mode
    launch = true       # desktop mode: false if NVDA is started elsewhere and connects itself
    nvda_dir = 'C:\\nvda'  # desktop mode: a portable NVDA (default: the one asc get-nvda made)
    share = ['C:\\src\\app']  # sandbox mode: host folders, read-only at C:\\share\\<name>

A target that isn't listed gets the defaults for `--mode` (watch if left out).
`--nvda-setting` values are added at `asc start`; they are not kept in the file.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from agent_speech_check.errors import ModeMismatch, TargetsFileError, UsageError, bad_field
from agent_speech_check.nvda_config import Setting
from agent_speech_check.validate import Fields

MODES = ("watch", "desktop", "sandbox", "shared")
DEFAULT_TARGET = "default"
WATCH_PORT = 6837
# Fixed, so the one firewall rule `asc sandbox-setup` makes keeps matching.
SANDBOX_PORT = 16837
_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")
_KEYS = ("mode", "port", "keys", "launch", "nvda_dir", "share")


@dataclass(frozen=True)
class Target:
    name: str
    mode: str
    port: int  # 0: pick a free port
    keys: bool
    launch: bool
    nvda_dir: str | None = None
    share: tuple[str, ...] = ()
    settings: tuple[Setting, ...] = ()
    # Shared mode only: the user passed --i-understand at asc start. Never
    # read from targets.toml, so consent can't be given once and forgotten.
    consent: bool = False

    def to_json(self) -> str:
        data = asdict(self)
        data["settings"] = [f"{'.'.join(s.path)}={s.value}" for s in self.settings]
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str) -> Target:
        """The daemon re-checks the target it is given, since it is an edge too."""
        try:
            raw = json.loads(text)
        except ValueError as error:
            raise bad_field(UsageError, "daemon target", "JSON", str(error)) from None
        where = "daemon target"
        f = Fields(raw, where, UsageError, ("name", *_KEYS, "settings", "consent"))
        settings = _strings(f, "settings")
        target = Target(
            check_name(f.text("name")),
            f.choice("mode", MODES),
            f.number("port", low=0, high=65535),
            keys=f.flag("keys"),
            launch=f.flag("launch"),
            nvda_dir=f.opt_text("nvda_dir", max_len=1000),
            share=_strings(f, "share"),
            settings=tuple(Setting.parse(s) for s in settings),
            consent=f.flag("consent") if f.has("consent") else False,
        )
        return _checked(target, where)

    @property
    def launches_portable(self) -> bool:
        """asc starts a portable NVDA on this computer (desktop mode)."""
        return self.mode == "desktop" and self.launch

    @property
    def launches_sandbox(self) -> bool:
        """asc starts NVDA inside Windows Sandbox (sandbox mode)."""
        return self.mode == "sandbox" and self.launch

    @property
    def launches_nvda(self) -> bool:
        return self.launches_portable or self.launches_sandbox

    def with_settings(self, settings: tuple[Setting, ...]) -> Target:
        return replace(self, settings=settings)

    def with_consent(self, consent: bool) -> Target:
        return replace(self, consent=consent)


def _strings(f: Fields, key: str) -> tuple[str, ...]:
    items = f.items(key) if f.has(key) else []
    if not all(isinstance(item, str) and item for item in items):
        raise bad_field(UsageError, f"{f.where} {key}", "a list of strings", items)
    return tuple(items)


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise bad_field(UsageError, "target name", "1 to 32 letters, digits, - or _", name)
    return name


def _checked(target: Target, where: str) -> Target:
    if target.mode == "watch" and target.keys:
        raise bad_field(UsageError, f"{where} keys", "false in watch mode", target.keys)
    return target


def defaults(name: str, mode: str) -> Target:
    ports = {"watch": WATCH_PORT, "shared": WATCH_PORT, "sandbox": SANDBOX_PORT}
    return Target(
        name,
        mode,
        ports.get(mode, 0),
        keys=mode != "watch",
        launch=mode in ("desktop", "sandbox"),
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
    target = Target(
        name,
        mode,
        f.number("port", low=1, high=65535) if f.has("port") else base.port,
        f.flag("keys") if f.has("keys") else base.keys,
        f.flag("launch") if f.has("launch") else base.launch,
        f.opt_text("nvda_dir", max_len=1000),
        _strings(f, "share"),
    )
    return _checked(target, where)


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
