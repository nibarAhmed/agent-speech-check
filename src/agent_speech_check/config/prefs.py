"""Every setting `config.toml` can hold, in the order the file lists them.

`asc setup`, `asc config` and the file loader all read this table, so a setting
exists in one place. A setting's scope says where it may live:
- `global`: top level (`mode`) or `[defaults]`
- `per mode`: a table named after the mode, like `[desktop]`

A command default in `[defaults]` gives way to its flag on the command line.
"""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check.daemon import ipc
from agent_speech_check.errors import UsageError, bad_field, show
from agent_speech_check.inputs import keys
from agent_speech_check.validate import Fields

GLOBAL = "global"
PER_MODE = "per mode"

MODES = ("watch", "desktop", "shared", "remote")


def default_mode(platform: str) -> str:
    """On Linux desktop mode starts Orca."""
    return "watch" if platform == "win32" else "desktop"


DEFAULT_MODE = default_mode(sys.platform)

LAYOUTS = ("desktop", "laptop")
# What the screen reader's machine runs: NVDA on Windows, Orca on Linux.
OSES = ("windows", "linux")

type Value = str | int | bool | list[str]


@dataclass(frozen=True)
class Pref:
    name: str
    scope: str
    kind: str  # bool, int, choice, destination, hotkey, path or settings
    default: str | int | bool | None = None  # None: none, or it depends on the mode
    low: int = 0
    high: int = 0
    choices: tuple[str, ...] = ()

    @property
    def is_list(self) -> bool:
        return self.kind == "settings"

    def check(self, raw: object, where: str) -> Value:  # noqa: PLR0911 - one return per kind
        """A value from the file, checked on its own. Rules across settings
        (ssh outside remote mode, say) are checked on the whole table."""
        f = Fields({self.name: raw}, where, UsageError)
        if self.kind == "bool":
            return f.flag(self.name)
        if self.kind == "int":
            return f.number(self.name, low=self.low, high=self.high)
        if self.kind == "choice":
            return f.choice(self.name, self.choices)
        if self.kind == "destination":
            return _destination(f.text(self.name, max_len=255), f"{where} {self.name}")
        if self.kind == "path":
            path = f.text(self.name, max_len=1000)
            # Relative to what? The daemon's folder or this file's would both surprise.
            if not Path(path).is_absolute():
                raise bad_field(
                    UsageError, f"{where} {self.name}", "a full path, like C:\\nvda", raw
                )
            return path
        if self.is_list:
            return list(f.strings(self.name))
        # What is left is a hotkey.
        try:
            return keys.parse_hotkey(f.text(self.name, max_len=40)).text
        except UsageError as error:
            raise UsageError(f"{where} {self.name}: {error.message}", hint=error.hint) from None

    def parse(self, values: list[str]) -> Value:
        """`asc config set` text into the value the file holds. The whole file
        is checked after, so this only converts."""
        if self.kind == "path" and not all(v.strip() for v in values):
            # `Path("").absolute()` is the current folder, which nobody meant.
            raise UsageError(f"setting {show(self.name)}: a path can't be empty")
        if self.is_list:
            return list(values)
        if len(values) != 1:
            raise UsageError(f"setting {show(self.name)}: takes one value")
        text = values[0]
        if self.kind == "bool":
            if text.lower() not in ("true", "false"):
                raise UsageError(f"setting {show(self.name)}: expected true or false")
            return text.lower() == "true"
        if self.kind == "int":
            try:
                return int(text)
            except ValueError:
                raise UsageError(f"setting {show(self.name)}: expected a whole number") from None
        if self.kind == "path":
            return str(Path(text).absolute())
        if self.kind == "hotkey":
            try:
                text = keys.parse_hotkey(text).text
            except UsageError as error:
                raise UsageError(
                    f"setting {show(self.name)}: {error.message}", hint=error.hint
                ) from None
        return text


PREFS: tuple[Pref, ...] = (
    Pref("mode", GLOBAL, "choice", DEFAULT_MODE, choices=MODES),
    Pref("panic_key", GLOBAL, "hotkey", keys.PANIC_KEY),
    Pref("port", PER_MODE, "int", low=1, high=65535),
    Pref("os", PER_MODE, "choice", choices=OSES),
    Pref("ssh", PER_MODE, "destination"),
    Pref("nvda_dir", PER_MODE, "path"),
    Pref("nvda_settings", PER_MODE, "settings"),
    Pref("settle_ms", GLOBAL, "int", ipc.DEFAULT_SETTLE_MS, *ipc.SETTLE_MS),
    Pref("timeout_ms", GLOBAL, "int", ipc.DEFAULT_TIMEOUT_MS, 1, ipc.MAX_TIMEOUT_MS),
    Pref("launch_timeout_ms", GLOBAL, "int", ipc.DEFAULT_LAUNCH_TIMEOUT_MS, 1, ipc.MAX_TIMEOUT_MS),
    Pref("keys_layout", GLOBAL, "choice", "desktop", choices=LAYOUTS),
    Pref("connect", GLOBAL, "bool", True),
    Pref("auto_update", GLOBAL, "bool", True),
)
BY_NAME = {pref.name: pref for pref in PREFS}

# Keys each part of the file may hold.
MODE_KEYS = tuple(p.name for p in PREFS if p.scope == PER_MODE)
DEFAULTS_KEYS = tuple(p.name for p in PREFS if p.scope == GLOBAL and p.name != "mode")


def _destination(text: str, where: str) -> str:
    """What ssh connects to. Never an option: ssh would read `-oProxyCommand=...` as one."""
    if text.startswith("-") or any(c.isspace() or not c.isprintable() for c in text):
        raise bad_field(UsageError, where, "an ssh destination, like me@vm", text)
    return text


def find(name: str) -> Pref:
    pref = BY_NAME.get(name)
    if pref is None:
        suggestions = difflib.get_close_matches(name, BY_NAME, n=3)
        hint = f"did you mean {', '.join(suggestions)}?" if suggestions else "asc config list"
        raise UsageError(f"unknown setting {show(name)}", hint=hint)
    return pref
