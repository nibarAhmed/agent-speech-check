"""Targets: what `asc start` starts, from a mode and its settings in `config.toml`.

`config.toml` sits in the state folder (`%LOCALAPPDATA%\\agent-speech-check`).
`asc setup` and `asc config` write it; `prefs.py` lists every setting it holds:

    mode = "sandbox"

    [defaults]
    settle_ms = 600

    [sandbox]
    share = ['C:\\src\\app']
    nvda_settings = ["keyboard.speakTypedCharacters=False"]

`mode` is what `asc start` uses without `--mode` (watch if not set). A mode
without a table gets its defaults. `--nvda-setting` and `--window` are added at
`asc start` and are not kept. The whole file is checked when it is read, so a
bad table fails every command, not only the ones that use it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import tomlkit
import tomlkit.exceptions

from agent_speech_check.config import prefs
from agent_speech_check.config.prefs import MODES, WINDOWS
from agent_speech_check.errors import (
    FileUnusable,
    TargetsFileError,
    UsageError,
    bad_field,
)
from agent_speech_check.inputs.keys import PANIC_KEY, Hotkey, parse_hotkey
from agent_speech_check.nvda import nvda_config
from agent_speech_check.nvda.nvda_config import Setting
from agent_speech_check.validate import Fields

WATCH_PORT = 6837
# Fixed, so the one firewall rule `asc sandbox-setup` makes keeps matching.
SANDBOX_PORT = 16837
_PANIC = parse_hotkey(PANIC_KEY)


@dataclass(frozen=True)
class Target:
    mode: str
    port: int  # 0: pick a free port
    launch: bool
    nvda_dir: str | None = None
    share: tuple[str, ...] = ()
    window: str | None = None  # sandbox mode; None: the default, tray
    settings: tuple[Setting, ...] = ()
    # Shared mode only: the user passed --i-understand at asc start. Never
    # read from config.toml, so consent can't be given once and forgotten.
    consent: bool = False
    # Global in config.toml, so `Config.resolve` gives every mode the same one.
    panic_key: Hotkey = _PANIC

    def to_json(self) -> str:
        data = asdict(self)
        data["settings"] = [setting.text for setting in self.settings]
        data["panic_key"] = self.panic_key.text
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str) -> Target:
        """The daemon re-checks the target it is given, since it is an edge too."""
        try:
            raw = json.loads(text)
        except ValueError as error:
            raise bad_field(UsageError, "daemon target", "JSON", str(error)) from None
        where = "daemon target"
        f = Fields(raw, where, UsageError, tuple(field.name for field in fields(Target)))
        settings = f.opt_strings("settings")
        target = Target(
            f.choice("mode", MODES),
            f.number("port", low=0, high=65535),
            launch=f.flag("launch"),
            nvda_dir=f.opt_text("nvda_dir", max_len=1000),
            share=f.opt_strings("share"),
            window=f.opt_text("window", max_len=20),
            settings=tuple(Setting.parse(s) for s in settings),
            consent=f.flag("consent"),
            panic_key=parse_hotkey(f.text("panic_key", max_len=40)),
        )
        return _checked(target, where)

    @property
    def keys(self) -> bool:
        """asc may send keys: every mode but watch."""
        return self.mode != "watch"

    @property
    def launches_portable(self) -> bool:
        """asc starts a portable NVDA on this computer (desktop mode)."""
        return self.mode == "desktop" and self.launch

    @property
    def takes_panic_key(self) -> bool:
        """asc's keys land in this Windows session (shared mode, or desktop mode's
        own NVDA), so a panic hotkey must be able to stop it."""
        return self.mode == "shared" or self.launches_portable

    @property
    def launches_sandbox(self) -> bool:
        """asc starts NVDA inside Windows Sandbox (sandbox mode)."""
        return self.mode == "sandbox" and self.launch

    @property
    def launches_nvda(self) -> bool:
        return self.launches_portable or self.launches_sandbox

    @property
    def sandbox_window(self) -> str:
        return self.window or "tray"

    def with_settings(self, settings: tuple[Setting, ...]) -> Target:
        return replace(self, settings=settings)

    def with_consent(self, consent: bool) -> Target:
        return replace(self, consent=consent)

    def with_window(self, window: str | None) -> Target:
        if window is None:
            return self
        return _checked(replace(self, window=window), "--window")


def _checked(target: Target, where: str) -> Target:
    if target.nvda_dir is not None and not target.launches_nvda:
        raise bad_field(
            UsageError, f"{where} nvda_dir", "only for an NVDA that asc launches", target.nvda_dir
        )
    if target.settings and not target.launches_nvda:
        raise bad_field(
            UsageError,
            f"{where} nvda_settings",
            "only for an NVDA that asc launches",
            [setting.text for setting in target.settings],
        )
    if target.window is not None:
        if not target.launches_sandbox:
            raise bad_field(UsageError, f"{where} window", "only in sandbox mode", target.window)
        if target.window not in WINDOWS:
            raise bad_field(
                UsageError, f"{where} window", f"one of {', '.join(WINDOWS)}", target.window
            )
    return target


def defaults(mode: str) -> Target:
    ports = {"watch": WATCH_PORT, "shared": WATCH_PORT, "sandbox": SANDBOX_PORT}
    return Target(
        mode,
        ports.get(mode, 0),
        launch=mode in ("desktop", "sandbox"),
    )


def parse(text: str, where: str) -> dict[str, Any]:
    """TOML text as plain Python values. `unwrap` matters: tomlkit's own types
    aren't all plain (its `Bool` isn't a `bool`), and `Fields` checks types."""
    try:
        return tomlkit.parse(text).unwrap()
    except tomlkit.exceptions.ParseError as error:
        raise TargetsFileError(where, str(error)) from None


def _read(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise FileUnusable.from_os(path, error) from None
    try:
        # -sig: some Windows editors start the file with a byte order mark.
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        line = data[: error.start].count(b"\n") + 1
        raise TargetsFileError(str(path), f"not UTF-8 (line {line})") from None
    return parse(text, str(path))


def _from_table(mode: str, raw: object, file: str) -> Target:
    where = f"{file}: [{mode}]"
    f = Fields(raw, where, UsageError, prefs.MODE_KEYS)
    values = _checked_values(f, prefs.MODE_KEYS)
    settings = tuple(
        Setting.parse(text, f"{where} nvda_settings") for text in values.get("nvda_settings", [])
    )
    nvda_config.check(settings)
    # Settings named like a `Target` field are kept as they are (lists frozen).
    kept: dict[str, Any] = {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in values.items()
        if key in _TARGET_FIELDS
    }
    target = replace(defaults(mode), **kept, settings=settings)
    return _checked(target, where)


_TARGET_FIELDS = frozenset(field.name for field in fields(Target))


def _checked_values(f: Fields, names: tuple[str, ...]) -> dict[str, Any]:
    """Each setting that is there, checked against its entry in `prefs`."""
    return {name: prefs.BY_NAME[name].check(f.raw[name], f.where) for name in names if f.has(name)}


@dataclass(frozen=True)
class Config:
    """The whole of `config.toml`, checked. `data` is the file as read, for
    `asc config` to change and write back."""

    data: Mapping[str, Any] = field(default_factory=dict)
    mode: str = "watch"
    defaults: Mapping[str, prefs.Value] = field(default_factory=dict)
    modes: Mapping[str, Target] = field(default_factory=dict)

    @classmethod
    def from_data(cls, data: dict[str, Any], file: str) -> Config:
        top = Fields(data, file, UsageError, ("mode", "defaults", *MODES))
        mode = top.choice("mode", MODES) if top.has("mode") else "watch"
        globals_ = Fields(
            data.get("defaults", {}), f"{file}: [defaults]", UsageError, prefs.DEFAULTS_KEYS
        )
        modes = {name: _from_table(name, data[name], file) for name in MODES if name in data}
        return cls(data, mode, _checked_values(globals_, prefs.DEFAULTS_KEYS), modes)

    def resolve(self, mode: str | None) -> Target:
        """What `asc start` starts: `mode`, or the `mode` setting, with its table."""
        chosen = mode or self.mode
        target = self.modes.get(chosen) or defaults(chosen)
        return replace(target, panic_key=parse_hotkey(str(self.value("panic_key")[0])))

    def value(self, name: str) -> tuple[prefs.Value | None, str]:
        """A global setting and where it came from: global or default."""
        if name == "mode":
            return self.mode, "global" if "mode" in self.data else "default"
        if name in self.defaults:
            return self.defaults[name], "global"
        return prefs.BY_NAME[name].default, "default"


def load(path: Path) -> Config:
    return Config.from_data(_read(path), path.name)
