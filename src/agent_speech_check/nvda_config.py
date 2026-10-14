"""`nvda.ini` for an NVDA that `asc` launches. Never written into the user's own config.

It makes NVDA start unattended and quiet, and connect out to the relay as the
follower (see `docs/protocol.md`):
- no welcome dialog, update check or usage-stats question
- the silent synth, and NVDA's own sounds at volume 0
- the desktop keyboard layout, which `keys.py` assumes
- the browse/focus mode sounds on, so the daemon can track the mode
- Remote Access set to auto-connect to the relay, with its cert pre-trusted

`--nvda-setting section.key=value` adds anything else (verbosity, punctuation).
Keys `asc` writes itself are refused, so a setting can never break the above.
The ini is built as data and written by configobj, which NVDA reads it with, so
`--nvda-setting` text is never pasted in raw and every value is quoted as NVDA needs.

A portable NVDA's own config (`userConfig`) is copied into the private config
folder first, add-ons and all, so it speaks with the user's settings. Its
`nvda.ini` is read, the settings above are put on top, and its whole `[remote]`
section is dropped. `profileTriggers.ini` is left out, because a profile that
switches on for an app could change the synth or keyboard layout.
"""

from __future__ import annotations

import copy
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import configobj

from agent_speech_check.errors import BadSetting, UserConfigUnreadable

# configobj reads `a, b` as a list.
type Section = dict[str, "str | list[str] | Section"]

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_FORBIDDEN = ("\r", "\n", "\0")
USER_CONFIG = "userConfig"
_NOT_COPIED = frozenset({"nvda.ini", "profileTriggers.ini"})


@dataclass(frozen=True)
class Setting:
    path: tuple[str, ...]  # sections, then the key
    value: str
    # Where it came from, for errors: the flag, or the config.toml table.
    where: str = field(default="--nvda-setting", compare=False)

    @classmethod
    def parse(cls, text: str, where: str = "--nvda-setting") -> Setting:
        name, sep, value = text.partition("=")
        name, value = name.strip(), value.strip()
        if not sep:
            raise BadSetting(text, "expected section.key=value", where)
        path = tuple(name.split("."))
        if len(path) < 2 or not all(_NAME.fullmatch(part) for part in path):  # noqa: PLR2004 - section and key
            raise BadSetting(text, "names are letters, digits and _, joined by dots", where)
        if any(c in value for c in _FORBIDDEN) or value.startswith("["):
            raise BadSetting(text, "the value can't contain line breaks or start with [", where)
        if '"' in value and "'" in value:
            raise BadSetting(text, "the value can't contain both kinds of quote", where)
        setting = cls(path, value, where)
        if setting.owned:
            raise BadSetting(text, "asc sets this itself", where)
        return setting

    @property
    def key(self) -> tuple[str, ...]:
        """The path as NVDA compares it: ignoring case."""
        return tuple(part.lower() for part in self.path)

    @property
    def text(self) -> str:
        return f"{'.'.join(self.path)}={self.value}"

    @property
    def owned(self) -> bool:
        if self.key[0] == "remote":
            return True
        owned = _flatten(base_config(_ANY_RELAY))
        return any(self.key == tuple(p.lower() for p in key) for key in owned)


def merge(kept: tuple[Setting, ...], given: tuple[Setting, ...]) -> tuple[Setting, ...]:
    """A target's kept settings, then the ones given at start. A given setting
    replaces a kept one with the same key."""
    replaced = {setting.key for setting in given}
    return tuple(s for s in kept if s.key not in replaced) + given


def _flatten(section: Section, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for name, value in section.items():
        if isinstance(value, dict):
            keys += _flatten(value, (*prefix, name))
        else:
            keys.append((*prefix, name))
    return keys


@dataclass(frozen=True)
class RelayAddress:
    """Where NVDA connects: the relay's address and port, channel key and cert."""

    host: str
    port: int
    channel: str
    fingerprint: str


# Stands in for the real relay where only the config's shape matters.
_ANY_RELAY = RelayAddress("", 1, "", "")


def base_config(relay: RelayAddress) -> Section:
    host = f"{relay.host}:{relay.port}"
    return {
        "general": {"showWelcomeDialogAtStartup": "False"},
        "update": {
            "autoCheckForUpdates": "False",
            "askedAllowUsageStats": "True",
            "allowUsageStats": "False",
        },
        "speech": {"synth": "silence"},
        "audio": {"soundVolumeFollowsVoice": "False", "soundVolume": "0"},
        "keyboard": {"keyboardLayout": "desktop"},
        "virtualBuffers": {"passThroughAudioIndication": "True"},
        "remote": {
            "enabled": "True",
            "controlServer": {
                "autoconnect": "True",
                "selfHosted": "False",
                "connectionMode": "0",
                "host": host,
                "port": str(relay.port),
                "key": relay.channel,
            },
            "trustedCertificates": {host: relay.fingerprint},
        },
    }


# ---------------------------------------------------------------- the user's own config


def parse_ini(text: str, where: str) -> Section:
    """Read ini text the way NVDA reads `nvda.ini`, refusing what asc couldn't write back."""
    lines = text.splitlines()
    try:
        config = configobj.ConfigObj(lines, interpolation=False, raise_errors=True)
    except configobj.ConfigObjError as error:
        raise UserConfigUnreadable(where, error.line_number) from None
    try:
        config.write()
    except configobj.ConfigObjError:
        # A value holding both ''' and """ can't be quoted. Only a one-line
        # unquoted value can hold both, so its line is the one that does.
        both = (n for n, line in enumerate(lines, 1) if "'''" in line and '"""' in line)
        raise UserConfigUnreadable(where, next(both, None)) from None
    section: Section = config.dict()
    return section


def source_config(nvda_folder: Path) -> Path:
    """Where a portable NVDA keeps its own config."""
    return nvda_folder / USER_CONFIG


def user_ini(source: Path) -> Section | None:
    """The `nvda.ini` in a config folder, parsed; None if there is none."""
    path = source / "nvda.ini"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UserConfigUnreadable(str(path), data[: error.start].count(b"\n") + 1) from None
    return parse_ini(text, str(path))


def _overlay(base: Section, top: Section) -> None:
    for name, value in top.items():
        current = base.get(name)
        if isinstance(value, dict) and isinstance(current, dict):
            _overlay(current, value)
        else:
            base[name] = value


def _merged(
    relay: RelayAddress, settings: tuple[Setting, ...], user: Section | None = None
) -> Section:
    config = {k: copy.deepcopy(v) for k, v in (user or {}).items() if k.lower() != "remote"}
    _overlay(config, base_config(relay))
    for setting in settings:
        section = config
        *sections, key = setting.path
        for name in sections:
            child = section.setdefault(name, {})
            if not isinstance(child, dict):
                raise BadSetting(
                    ".".join(setting.path), f"{name} is a key, not a section", setting.where
                )
            section = child
        if isinstance(section.get(key), dict):
            raise BadSetting(
                ".".join(setting.path), f"{key} is a section, not a key", setting.where
            )
        section[key] = setting.value
    return config


def check(settings: tuple[Setting, ...], user: Section | None = None) -> None:
    """Refuse settings that clash with each other or with the user's own
    config, before anything starts."""
    _merged(_ANY_RELAY, settings, user)


def render(
    relay: RelayAddress, settings: tuple[Setting, ...] = (), user: Section | None = None
) -> str:
    # Unindented, as NVDA writes it. configobj puts a section's keys before its subsections.
    config = configobj.ConfigObj(
        _merged(relay, settings, user), interpolation=False, indent_type=""
    )
    return "\n".join(config.write()) + "\n"


def write(
    config_dir: Path, relay: RelayAddress, settings: tuple[Setting, ...], source: Path | None = None
) -> None:
    """Make `config_dir` a fresh NVDA config folder: a copy of `source` (if it
    exists) with `asc`'s `nvda.ini`. `source` itself is only read."""
    user = user_ini(source) if source is not None else None
    if config_dir.exists():
        shutil.rmtree(config_dir)
    if source is not None and source.is_dir():

        def skip(directory: str, names: list[str]) -> set[str]:
            # Only at the top: an add-on may ship files with these names.
            return {n for n in names if n in _NOT_COPIED} if Path(directory) == source else set()

        shutil.copytree(source, config_dir, ignore=skip)
    else:
        config_dir.mkdir(parents=True)
    (config_dir / "nvda.ini").write_text(render(relay, settings, user), encoding="utf-8")
