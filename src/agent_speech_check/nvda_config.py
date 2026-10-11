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
The ini is built as data and rendered; `--nvda-setting` text is never pasted in raw.

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
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check.errors import BadSetting, UserConfigUnreadable


@dataclass(frozen=True)
class Raw:
    """A value from the user's own `nvda.ini`, written back exactly as it was."""

    text: str


type Section = dict[str, "str | Raw | Section"]

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_FORBIDDEN = ("\r", "\n", "\0")
_HEADER = re.compile(r"(\[+)([^\[\]]+)(\]+)\s*(?:#.*)?")
USER_CONFIG = "userConfig"
_NOT_COPIED = frozenset({"nvda.ini", "profileTriggers.ini"})


@dataclass(frozen=True)
class Setting:
    path: tuple[str, ...]  # sections, then the key
    value: str

    @classmethod
    def parse(cls, text: str) -> Setting:
        name, sep, value = text.partition("=")
        name, value = name.strip(), value.strip()
        if not sep:
            raise BadSetting(text, "expected section.key=value")
        path = tuple(name.split("."))
        if len(path) < 2 or not all(_NAME.fullmatch(part) for part in path):  # noqa: PLR2004 - section and key
            raise BadSetting(text, "names are letters, digits and _, joined by dots")
        if any(c in value for c in _FORBIDDEN) or value.startswith("["):
            raise BadSetting(text, "the value can't contain line breaks or start with [")
        if '"' in value and "'" in value:
            raise BadSetting(text, "the value can't contain both kinds of quote")
        setting = cls(path, value)
        if setting.owned:
            raise BadSetting(text, "asc sets this itself")
        return setting

    @property
    def owned(self) -> bool:
        lowered = tuple(p.lower() for p in self.path)
        if lowered[0] == "remote":
            return True
        owned = _flatten(base_config(_ANY_RELAY))
        return any(lowered == tuple(p.lower() for p in key) for key in owned)


def _flatten(section: Section, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for name, value in section.items():
        if isinstance(value, dict):
            keys += _flatten(value, (*prefix, name))
        else:
            keys.append((*prefix, name))
    return keys


@dataclass(frozen=True)
class Relay:
    """Where NVDA connects: the relay's address and port, channel key and cert."""

    host: str
    port: int
    channel: str
    fingerprint: str


# Stands in for the real relay where only the config's shape matters.
_ANY_RELAY = Relay("", 1, "", "")


def base_config(relay: Relay) -> Section:
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
            "trustedCertificates": {f'"{host}"': relay.fingerprint},
        },
    }


def _quote(value: str) -> str:
    """configobj reads an unquoted comma as a list, and # as a comment."""
    if value and not any(c in value for c in ",#\"'") and value == value.strip():
        return value
    return f"'{value}'" if '"' in value else f'"{value}"'


def _render(section: Section, depth: int, lines: list[str]) -> None:
    for name, value in section.items():
        if isinstance(value, Raw):
            lines.append(f"{name} = {value.text}")
        elif isinstance(value, str):
            quoted = value if name.startswith('"') else _quote(value)
            lines.append(f"{name} = {quoted}")
    for name, value in section.items():
        if isinstance(value, dict):
            lines.append(f"{'[' * depth}{name}{']' * depth}")
            _render(value, depth + 1, lines)


# ---------------------------------------------------------------- the user's own config


def _split_key(line: str) -> tuple[str, str] | None:
    """`key = value` at the first `=` outside quotes, or None."""
    quote = None
    for i, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "=":
            key = line[:i].strip()
            return (key, line[i + 1 :].strip()) if key else None
    return None


def parse_ini(text: str, where: str) -> Section:
    """Read configobj ini as NVDA writes it, keeping every value's raw text."""
    root: Section = {}
    stack = [root]
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        header = _HEADER.fullmatch(line)
        if header:
            depth = len(header[1])
            if depth != len(header[3]) or depth > len(stack):
                raise UserConfigUnreadable(where, number)
            del stack[depth:]
            child: Section = {}
            stack[-1][header[2].strip()] = child
            stack.append(child)
            continue
        pair = _split_key(line)
        if pair is None or pair[1].startswith(("'''", '"""')):
            raise UserConfigUnreadable(where, number)
        stack[-1][pair[0]] = Raw(pair[1])
    return root


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


def _merged(relay: Relay, settings: tuple[Setting, ...], user: Section | None = None) -> Section:
    config: Section = {}
    if user is not None:
        config = {k: copy.deepcopy(v) for k, v in user.items() if k.lower() != "remote"}
    _overlay(config, base_config(relay))
    for setting in settings:
        section = config
        *sections, key = setting.path
        for name in sections:
            child = section.setdefault(name, {})
            if not isinstance(child, dict):
                raise BadSetting(".".join(setting.path), f"{name} is a key, not a section")
            section = child
        if isinstance(section.get(key), dict):
            raise BadSetting(".".join(setting.path), f"{key} is a section, not a key")
        section[key] = setting.value
    return config


def check(settings: tuple[Setting, ...], user: Section | None = None) -> None:
    """Refuse settings that clash with each other or with the user's own
    config, before anything starts."""
    _merged(_ANY_RELAY, settings, user)


def render(relay: Relay, settings: tuple[Setting, ...] = (), user: Section | None = None) -> str:
    lines: list[str] = []
    _render(_merged(relay, settings, user), 1, lines)
    return "\n".join(lines) + "\n"


def write(
    config_dir: Path, relay: Relay, settings: tuple[Setting, ...], source: Path | None = None
) -> None:
    """Make `config_dir` a fresh NVDA config folder: a copy of `source` (if it
    exists) with `asc`'s `nvda.ini`. `source` itself is only read."""
    user = user_ini(source) if source is not None else None
    if config_dir.exists():
        shutil.rmtree(config_dir)
    if source is not None and source.is_dir():

        def skip(directory: str, names: list[str]) -> set[str]:
            return set(_NOT_COPIED & set(names)) if Path(directory) == source else set()

        shutil.copytree(source, config_dir, ignore=skip)
    else:
        config_dir.mkdir(parents=True)
    (config_dir / "nvda.ini").write_text(render(relay, settings, user), encoding="utf-8")
