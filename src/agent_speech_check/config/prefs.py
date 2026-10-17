"""Every setting `config.toml` can hold, in the order the file lists them.

`asc setup`, `asc config` and the file loader all read this table, so a setting
exists in one place. A setting's scope says where it may live:
- `global`: top level (`mode`) or `[defaults]`
- `per mode`: a table named after the mode, like `[sandbox]`

A command default in `[defaults]` gives way to its flag on the command line.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check.daemon import ipc
from agent_speech_check.errors import (
    BadInFile,
    BadPreference,
    UnknownPreference,
    UsageError,
    bad_field,
)
from agent_speech_check.inputs import keys
from agent_speech_check.validate import Fields

GLOBAL = "global"
PER_MODE = "per mode"

MODES = ("watch", "desktop", "sandbox", "shared")
# Where the sandbox window goes: hidden behind a tray icon, minimised on the
# taskbar, or left alone.
WINDOWS = ("tray", "taskbar", "show")
LAYOUTS = ("desktop", "laptop")

type Value = str | int | bool | list[str]


@dataclass(frozen=True)
class Pref:
    name: str
    scope: str
    kind: str  # bool, int, choice, hotkey, path, paths or settings
    default: str | int | bool | None = None  # None: none, or it depends on the mode
    low: int = 0
    high: int = 0
    choices: tuple[str, ...] = ()

    @property
    def is_list(self) -> bool:
        return self.kind in ("paths", "settings")

    def check(self, raw: object, where: str) -> Value:
        """A value from the file, checked on its own. Rules across settings
        (window outside sandbox mode, say) are checked on the whole table."""
        f = Fields({self.name: raw}, where, UsageError)
        if self.kind == "bool":
            return f.flag(self.name)
        if self.kind == "int":
            return f.number(self.name, low=self.low, high=self.high)
        if self.kind == "choice":
            return f.choice(self.name, self.choices)
        if self.kind in ("path", "paths"):
            one = self.kind == "path"
            paths = [f.text(self.name, max_len=1000)] if one else list(f.strings(self.name))
            # Relative to what? The daemon's folder or this file's would both surprise.
            if not all(Path(path).is_absolute() for path in paths):
                expected = "a full path, like C:\\nvda" if one else "full paths, like C:\\src\\app"
                raise bad_field(UsageError, f"{where} {self.name}", expected, raw)
            return paths[0] if one else paths
        if self.is_list:
            return list(f.strings(self.name))
        # What is left is a hotkey.
        try:
            return keys.parse_hotkey(f.text(self.name, max_len=40)).text
        except UsageError as error:
            field = f"{where} {self.name}"
            raise BadInFile(field, error.message, error.hint) from None

    def parse(self, values: list[str]) -> Value:
        """`asc config set` text into the value the file holds. The whole file
        is checked after, so this only converts."""
        if self.kind in ("path", "paths") and not all(v.strip() for v in values):
            # `Path("").absolute()` is the current folder, which nobody meant.
            raise BadPreference(self.name, "a path can't be empty")
        if self.is_list:
            return [str(Path(v).absolute()) if self.kind == "paths" else v for v in values]
        if len(values) != 1:
            raise BadPreference(self.name, "takes one value")
        text = values[0]
        if self.kind == "bool":
            if text.lower() not in ("true", "false"):
                raise BadPreference(self.name, "expected true or false")
            return text.lower() == "true"
        if self.kind == "int":
            try:
                return int(text)
            except ValueError:
                raise BadPreference(self.name, "expected a whole number") from None
        if self.kind == "path":
            return str(Path(text).absolute())
        if self.kind == "hotkey":
            try:
                text = keys.parse_hotkey(text).text
            except UsageError as error:
                raise BadPreference(self.name, error.message, error.hint) from None
        return text


PREFS: tuple[Pref, ...] = (
    Pref("mode", GLOBAL, "choice", "watch", choices=MODES),
    Pref("panic_key", GLOBAL, "hotkey", keys.PANIC_KEY),
    Pref("port", PER_MODE, "int", low=1, high=65535),
    Pref("launch", PER_MODE, "bool"),
    Pref("nvda_dir", PER_MODE, "path"),
    Pref("share", PER_MODE, "paths"),
    Pref("window", PER_MODE, "choice", choices=WINDOWS),
    Pref("nvda_settings", PER_MODE, "settings"),
    Pref("settle_ms", GLOBAL, "int", ipc.DEFAULT_SETTLE_MS, *ipc.SETTLE_MS),
    Pref("timeout_ms", GLOBAL, "int", ipc.DEFAULT_TIMEOUT_MS, 1, ipc.MAX_TIMEOUT_MS),
    Pref("launch_timeout_ms", GLOBAL, "int", ipc.DEFAULT_LAUNCH_TIMEOUT_MS, 1, ipc.MAX_TIMEOUT_MS),
    Pref("keys_layout", GLOBAL, "choice", "desktop", choices=LAYOUTS),
)
BY_NAME = {pref.name: pref for pref in PREFS}

# Keys each part of the file may hold.
MODE_KEYS = tuple(p.name for p in PREFS if p.scope == PER_MODE)
DEFAULTS_KEYS = tuple(p.name for p in PREFS if p.scope == GLOBAL and p.name != "mode")


def find(name: str) -> Pref:
    pref = BY_NAME.get(name)
    if pref is None:
        raise UnknownPreference(name, difflib.get_close_matches(name, BY_NAME, n=3))
    return pref
