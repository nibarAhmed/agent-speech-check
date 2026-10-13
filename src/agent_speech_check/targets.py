"""Targets: named NVDA setups, listed in `config.toml` or made from defaults.

`config.toml` sits in the state folder (`%LOCALAPPDATA%\\agent-speech-check`).
`asc setup` and `asc config` write it; `prefs.py` lists every setting it holds:

    default = "app"

    [defaults]
    settle_ms = 600

    [targets.app]
    mode = "sandbox"
    share = ['C:\\src\\app']
    nvda_settings = ["speech.symbolLevel=300"]

A target that isn't listed gets the defaults for `--mode` (watch if left out).
`--nvda-setting`, `--nvda-dir` and `--window` are added at `asc start` and are
not kept. The whole file is checked when it is read, so a bad table fails every
command, not only the ones that use it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import tomlkit
import tomlkit.exceptions

from agent_speech_check import nvda_config, prefs
from agent_speech_check.errors import ModeMismatch, TargetsFileError, UsageError, bad_field
from agent_speech_check.nvda_config import Setting
from agent_speech_check.prefs import MODES, NAME_PATTERN, WINDOWS
from agent_speech_check.validate import Fields

DEFAULT_TARGET = "default"
WATCH_PORT = 6837
# Fixed, so the one firewall rule `asc sandbox-setup` makes keeps matching.
SANDBOX_PORT = 16837
_NAME = re.compile(NAME_PATTERN)


@dataclass(frozen=True)
class Target:
    name: str
    mode: str
    port: int  # 0: pick a free port
    keys: bool
    launch: bool
    nvda_dir: str | None = None
    share: tuple[str, ...] = ()
    window: str | None = None  # sandbox mode; None: the default, tray
    settings: tuple[Setting, ...] = ()
    # Shared mode only: the user passed --i-understand at asc start. Never
    # read from config.toml, so consent can't be given once and forgotten.
    consent: bool = False

    def to_json(self) -> str:
        data = asdict(self)
        data["settings"] = [setting.text for setting in self.settings]
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
        settings = _strings(f, "settings")
        target = Target(
            check_name(f.text("name")),
            f.choice("mode", MODES),
            f.number("port", low=0, high=65535),
            keys=f.flag("keys"),
            launch=f.flag("launch"),
            nvda_dir=f.opt_text("nvda_dir", max_len=1000),
            share=_strings(f, "share"),
            window=f.opt_text("window", max_len=20),
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

    @property
    def sandbox_window(self) -> str:
        return self.window or "tray"

    def with_settings(self, settings: tuple[Setting, ...]) -> Target:
        return replace(self, settings=settings)

    def with_consent(self, consent: bool) -> Target:
        return replace(self, consent=consent)

    def with_nvda_dir(self, nvda_dir: str | None) -> Target:
        if nvda_dir is None:
            return self
        # The daemon runs in its own folder, so a relative path must be made absolute here.
        return _checked(replace(self, nvda_dir=str(Path(nvda_dir).absolute())), "--nvda-dir")

    def with_window(self, window: str | None) -> Target:
        if window is None:
            return self
        return _checked(replace(self, window=window), "--window")


def _strings(f: Fields, key: str) -> tuple[str, ...]:
    return f.strings(key) if f.has(key) else ()


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise bad_field(UsageError, "target name", "1 to 32 letters, digits, - or _", name)
    return name


def _checked(target: Target, where: str) -> Target:
    if target.mode == "watch" and target.keys:
        raise bad_field(UsageError, f"{where} keys", "false in watch mode", target.keys)
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


def defaults(name: str, mode: str) -> Target:
    ports = {"watch": WATCH_PORT, "shared": WATCH_PORT, "sandbox": SANDBOX_PORT}
    return Target(
        name,
        mode,
        ports.get(mode, 0),
        keys=mode != "watch",
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
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        line = data[: error.start].count(b"\n") + 1
        raise TargetsFileError(str(path), f"not UTF-8 (line {line})") from None
    return parse(text, str(path))


def _from_table(name: str, raw: object, file: str) -> tuple[Target, dict[str, prefs.Value]]:
    """A target and its own command defaults."""
    where = f"{file}: [targets.{name}]"
    f = Fields(raw, where, UsageError, prefs.TARGET_KEYS)
    # Required: the mode decides every other default.
    mode = f.choice("mode", MODES)
    values = _checked_values(f, prefs.TARGET_KEYS)
    settings = tuple(
        Setting.parse(text, f"{where} nvda_settings") for text in values.get("nvda_settings", [])
    )
    nvda_config.check(settings)
    kept = {key: values[key] for key in _TARGET_FIELDS if key in values}
    target = replace(
        defaults(name, mode),
        **kept,
        share=tuple(values.get("share", [])),
        settings=settings,
    )
    overrides = {key: values[key] for key in prefs.COMMAND_DEFAULTS if key in values}
    return _checked(target, where), overrides


# Settings stored on `Target` as the file holds them.
_TARGET_FIELDS = ("port", "keys", "launch", "nvda_dir", "window")


def _checked_values(f: Fields, names: tuple[str, ...]) -> dict[str, Any]:
    """Each setting that is there, checked against its entry in `prefs`."""
    return {name: prefs.BY_NAME[name].check(f.raw[name], f.where) for name in names if f.has(name)}


@dataclass(frozen=True)
class Config:
    """The whole of `config.toml`, checked. `data` is the file as read, for
    `asc config` to change and write back."""

    data: Mapping[str, Any] = field(default_factory=dict)
    default: str = DEFAULT_TARGET
    defaults: Mapping[str, prefs.Value] = field(default_factory=dict)
    targets: Mapping[str, Target] = field(default_factory=dict)
    overrides: Mapping[str, Mapping[str, prefs.Value]] = field(default_factory=dict)

    @classmethod
    def from_data(cls, data: dict[str, Any], file: str) -> Config:
        top = Fields(data, file, UsageError, ("default", "defaults", "targets"))
        default = check_name(top.opt_text("default", max_len=32) or DEFAULT_TARGET)
        globals_ = Fields(
            data.get("defaults", {}), f"{file}: [defaults]", UsageError, prefs.DEFAULTS_KEYS
        )
        tables = Fields(data.get("targets", {}), f"{file}: [targets]", UsageError)
        targets: dict[str, Target] = {}
        overrides: dict[str, dict[str, prefs.Value]] = {}
        for name, raw in tables.raw.items():
            targets[name], overrides[name] = _from_table(check_name(name), raw, file)
        return cls(
            data, default, _checked_values(globals_, prefs.DEFAULTS_KEYS), targets, overrides
        )

    def resolve(self, name: str | None, mode: str | None) -> Target:
        """The target to use: listed here, or defaults for `mode`."""
        chosen = check_name(name or self.default)
        target = self.targets.get(chosen)
        if target is None:
            return defaults(chosen, mode or "watch")
        if mode is not None and mode != target.mode:
            raise ModeMismatch(chosen, target.mode, mode)
        return target

    def value(self, name: str, target: str | None = None) -> tuple[prefs.Value | None, str]:
        """A global or command default setting and where it came from: target,
        global or default."""
        own = self.overrides.get(target or self.default, {})
        if name in own:
            return own[name], "target"
        if name == "default":
            return self.default, "global" if "default" in self.data else "default"
        if name in self.defaults:
            return self.defaults[name], "global"
        return prefs.BY_NAME[name].default, "default"


def load(path: Path) -> Config:
    return Config.from_data(_read(path), path.name)
