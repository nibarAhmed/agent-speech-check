"""Changing and writing `config.toml`. `targets.py` reads and checks it.

Every write goes through `render`, which lays the file out the same way each
time, in `prefs.PREFS` order:
- a header comment, then `default`, then `[defaults]`, then `[targets.NAME]` by name
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

from agent_speech_check.errors import BadPreference, TargetNotConfigured
from agent_speech_check.paths import write_atomic
from agent_speech_check.prefs import GLOBAL, PREFS, TARGET, Pref, Value
from agent_speech_check.targets import Config, Target, check_name

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
    if "default" in data:
        doc["default"] = data["default"]
    if data.get("defaults"):
        doc["defaults"] = _table(data["defaults"])
    tables = data.get("targets", {})
    if tables:
        targets = tomlkit.table(is_super_table=True)
        for name in sorted(tables):
            targets[name] = _table(tables[name])
        doc["targets"] = targets
    return tomlkit.dumps(doc)


def save(path: Path, data: dict[str, Any]) -> Config:
    """Check `data`, then write it as `path` in one step."""
    config = Config.from_data(data, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, render(data))
    return config


# ---------------------------------------------------------------- changes


def owner(config: Config, pref: Pref, target: str | None) -> str | None:
    """The target whose table holds `pref`, or None for the global part."""
    if pref.scope == GLOBAL:
        if target is not None:
            raise BadPreference(pref.name, "is global, so it takes no --target")
        return None
    if pref.scope == TARGET:
        return check_name(target or config.default)
    return check_name(target) if target is not None else None


def label(where: str | None) -> str:
    return "global" if where is None else f"target {where}"


def _holder(data: dict[str, Any], pref: Pref, where: str | None) -> dict[str, Any]:
    if where is None:
        return data if pref.name == "default" else data.setdefault("defaults", {})
    tables: dict[str, Any] = data.setdefault("targets", {})
    if where not in tables:
        # A target is made by giving it a mode; every other setting needs one.
        if pref.name != "mode":
            raise TargetNotConfigured(where)
        tables[where] = {}
    table: dict[str, Any] = tables[where]
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
    if pref.name == "mode":
        raise BadPreference("mode", "every target needs one", hint="asc config remove-target NAME")
    data = copy.deepcopy(dict(config.data))
    _holder(data, pref, where).pop(pref.name, None)
    if not data.get("defaults", True):
        del data["defaults"]
    return data


def remove_target(config: Config, name: str) -> dict[str, Any]:
    data = copy.deepcopy(dict(config.data))
    if check_name(name) not in data.get("targets", {}):
        raise TargetNotConfigured(name)
    del data["targets"][name]
    if not data["targets"]:
        del data["targets"]
    return data


# ---------------------------------------------------------------- listing


def _target_value(target: Target, name: str) -> Value | None:
    if name == "nvda_settings":
        return [setting.text for setting in target.settings]
    if name == "share":
        return list(target.share)
    value: Value | None = getattr(target, name)
    return value


def entries(config: Config, target: str | None) -> list[tuple[str, Value | None, str]]:
    """Every setting for a target: name, value, and where the value comes from."""
    name = check_name(target or config.default)
    resolved = config.resolve(name, None)
    listed = config.data.get("targets", {}).get(name, {})
    rows: list[tuple[str, Value | None, str]] = []
    for pref in PREFS:
        if pref.scope == TARGET:
            source = "target" if pref.name in listed else "default"
            rows.append((pref.name, _target_value(resolved, pref.name), source))
        else:
            rows.append((pref.name, *config.value(pref.name, name)))
    return rows


def show(value: Value | None) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(value) if value else "none"
    return str(value)
