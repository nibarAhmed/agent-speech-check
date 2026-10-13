"""Every setting `config.toml` can hold, in the order the file lists them.

`asc setup`, `asc config` and the file loader all read this table, so a setting
exists in one place. A setting's scope says where it may live:
- `global`: top level (`default`) or `[defaults]`
- `target`: `[targets.NAME]`
- `both`: `[defaults]`, and a target may override it

A `both` setting is a command default. The order is the command line flag,
then the target's value, then `[defaults]`, then the built-in default.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check import ipc, nvda
from agent_speech_check.errors import BadPreference, UnknownPreference, UsageError, bad_field
from agent_speech_check.validate import Fields

GLOBAL = "global"
TARGET = "target"
BOTH = "both"

MODES = ("watch", "desktop", "sandbox", "shared")
# Where the sandbox window goes: hidden behind a tray icon, minimised on the
# taskbar, or left alone.
WINDOWS = ("tray", "taskbar", "show")
LAYOUTS = ("desktop", "laptop")
# A target name, which is also its folder name.
NAME_PATTERN = r"[A-Za-z0-9_-]{1,32}"

type Value = str | int | bool | list[str]


@dataclass(frozen=True)
class Pref:
    name: str
    scope: str
    kind: str  # bool, int, choice, text, path, paths or settings
    default: str | int | bool | None = None  # None: none, or it depends on the mode
    low: int = 0
    high: int = 0
    choices: tuple[str, ...] = ()
    pattern: str = ""  # text: what a valid value looks like

    @property
    def is_list(self) -> bool:
        return self.kind in ("paths", "settings")

    def check(self, raw: object, where: str) -> Value:
        """A value from the file, checked on its own. Rules across settings
        (keys in watch mode, say) are checked on the whole target."""
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
        value = f.text(self.name, max_len=40)
        if not re.fullmatch(self.pattern, value):
            raise bad_field(UsageError, f"{where} {self.name}", "a value like the default", value)
        return value

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
        return text


PREFS: tuple[Pref, ...] = (
    Pref("default", GLOBAL, "text", "default", pattern=NAME_PATTERN),
    Pref("nvda_version", GLOBAL, "text", nvda.VERSION, pattern=r"\d{4}\.\d+(\.\d+)?"),
    Pref("mode", TARGET, "choice", choices=MODES),
    Pref("port", TARGET, "int", low=1, high=65535),
    Pref("keys", TARGET, "bool"),
    Pref("launch", TARGET, "bool"),
    Pref("nvda_dir", TARGET, "path"),
    Pref("share", TARGET, "paths"),
    Pref("window", TARGET, "choice", choices=WINDOWS),
    Pref("nvda_settings", TARGET, "settings"),
    Pref("settle_ms", BOTH, "int", ipc.DEFAULT_SETTLE_MS, *ipc.SETTLE_MS),
    Pref("timeout_ms", BOTH, "int", ipc.DEFAULT_TIMEOUT_MS, 1, ipc.MAX_TIMEOUT_MS),
    Pref("launch_timeout_ms", BOTH, "int", ipc.DEFAULT_LAUNCH_TIMEOUT_MS, 1, ipc.MAX_TIMEOUT_MS),
    Pref("keys_layout", BOTH, "choice", "desktop", choices=LAYOUTS),
    Pref("keys_lang", BOTH, "text", "en", pattern=r"[A-Za-z]{2,3}(_[A-Za-z]{2,4})?"),
    Pref("keys_braille", BOTH, "bool", default=False),
)
BY_NAME = {pref.name: pref for pref in PREFS}

# Keys each part of the file may hold.
TARGET_KEYS = tuple(p.name for p in PREFS if p.scope in (TARGET, BOTH))
DEFAULTS_KEYS = tuple(p.name for p in PREFS if p.scope in (GLOBAL, BOTH) and p.name != "default")
COMMAND_DEFAULTS = tuple(p.name for p in PREFS if p.scope == BOTH)


def find(name: str) -> Pref:
    pref = BY_NAME.get(name)
    if pref is None:
        raise UnknownPreference(name, difflib.get_close_matches(name, BY_NAME, n=3))
    return pref
