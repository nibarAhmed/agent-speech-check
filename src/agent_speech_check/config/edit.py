"""Changing and writing `config.toml`. `targets.py` reads and checks it.

Every write goes through `render`, which lays the file out the same way each
time, in `prefs.PREFS` order:
- a header comment, then `mode`, then `[defaults]`, then a table per mode, in `MODES` order
- only the settings that were set; a missing key means the built-in default
- paths as literal strings (`'C:\\nvda'`), as the TOML spec suggests for Windows
  paths, unless the path holds a `'`
- an array that would make a long line goes one item per line

A change is made to a copy of the data, checked by reading it back into a
`Config`, and only then written: to a temporary file that replaces the old one.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Array, Table

from agent_speech_check.config.prefs import GLOBAL, MODES, PER_MODE, PREFS, Pref, Value
from agent_speech_check.config.targets import Config, Target
from agent_speech_check.errors import BadPreference, FileUnusable
from agent_speech_check.windows.paths import write_atomic

HEADER = "asc settings. Change them with asc config set, or start over with asc setup."
_LINE = 100


def _string(text: str, *, path: bool) -> tomlkit.items.String:
    return tomlkit.string(text, literal=path and "'" not in text)


def _item(pref: Pref, value: Value) -> object:
    if isinstance(value, list):
        array: Array = tomlkit.array()
        for text in value:
            array.append(_string(text, path=pref.kind == "paths"))
        if len(pref.name) + len(" = ") + len(array.as_string()) > _LINE:
            array.multiline(True)
        return array
    if isinstance(value, str):
        return _string(value, path=pref.kind == "path")
    return value


def _table(values: Mapping[str, Value]) -> Table:
    table = tomlkit.table()
    for pref in PREFS:
        if pref.name in values:
            table[pref.name] = _item(pref, values[pref.name])
    return table


def render(data: Mapping[str, Any]) -> str:
    doc = tomlkit.document()
    doc.add(tomlkit.comment(HEADER))
    if "mode" in data:
        doc["mode"] = data["mode"]
    for name in ("defaults", *MODES):
        if data.get(name):
            doc[name] = _table(data[name])
    return tomlkit.dumps(doc)


def save(path: Path, data: dict[str, Any]) -> Config:
    """Check `data`, then write it as `path` in one step."""
    config = Config.from_data(data, path.name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise FileUnusable.from_os(path.parent, error) from None
    write_atomic(path, render(data))
    return config


# ---------------------------------------------------------------- changes


def owner(config: Config, pref: Pref, mode: str | None) -> str | None:
    """The mode whose table holds `pref`, or None for the global part."""
    if pref.scope == GLOBAL:
        if mode is not None:
            raise BadPreference(pref.name, "is global, so it takes no --mode")
        return None
    return mode or config.mode


def label(where: str | None) -> str:
    return "global" if where is None else f"{where} mode"


def _holder(data: dict[str, Any], pref: Pref, where: str | None) -> dict[str, Any]:
    if where is None:
        return data if pref.name == "mode" else data.setdefault("defaults", {})
    table: dict[str, Any] = data.setdefault(where, {})
    return table


def set_value(
    config: Config, pref: Pref, values: list[str], where: str | None
) -> tuple[dict[str, Any], Value]:
    """The new data, and the value as it will be kept. `where` is from `owner`."""
    data = copy.deepcopy(dict(config.data))
    value = pref.parse(values)
    _holder(data, pref, where)[pref.name] = value
    return data, value


def unset_value(config: Config, pref: Pref, where: str | None) -> dict[str, Any]:
    """The new data. `where` is from `owner`."""
    data = copy.deepcopy(dict(config.data))
    _holder(data, pref, where).pop(pref.name, None)
    # An empty table says nothing, so it goes.
    for name in ("defaults", *MODES):
        if name in data and not data[name]:
            del data[name]
    return data


# ---------------------------------------------------------------- listing


def _target_value(target: Target, name: str) -> Value | None:
    if name == "nvda_settings":
        return [setting.text for setting in target.settings]
    if name == "share":
        return list(target.share)
    value: Value | None = getattr(target, name)
    return value


def entries(config: Config, mode: str | None) -> list[tuple[str, Value | None, str]]:
    """Every setting for a mode: name, value, and where the value comes from
    (the mode's table, global or default)."""
    chosen = mode or config.mode
    resolved = config.resolve(chosen)
    listed = config.data.get(chosen, {})
    rows: list[tuple[str, Value | None, str]] = []
    for pref in PREFS:
        if pref.scope == PER_MODE:
            source = chosen if pref.name in listed else "default"
            rows.append((pref.name, _target_value(resolved, pref.name), source))
        else:
            rows.append((pref.name, *config.value(pref.name)))
    return rows


def show(value: Value | None) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(value) if value else "none"
    return str(value)
