"""`nvda.ini` for an NVDA that `asc` launches. Never for the user's own NVDA.

It makes NVDA start unattended and quiet, and connect out to the relay as the
follower (see `docs/protocol.md`):
- no welcome dialog, update check or usage-stats question
- the silent synth, and NVDA's own sounds at volume 0
- the desktop keyboard layout, which `keys.py` assumes
- the browse/focus mode sounds on, so the daemon can track the mode
- Remote Access set to auto-connect to the relay, with its cert pre-trusted

`--nvda-setting section.key=value` adds anything else (verbosity, punctuation).
Keys `asc` writes itself are refused, so a setting can never break the above.
The ini is built as data and rendered; user text is never pasted in raw.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_speech_check.errors import BadSetting

type Section = dict[str, "str | Section"]

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_FORBIDDEN = ("\r", "\n", "\0")


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
        owned = _flatten(base_config("", 1, ""))
        return any(lowered == tuple(p.lower() for p in key) for key in owned)


def _flatten(section: Section, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for name, value in section.items():
        if isinstance(value, dict):
            keys += _flatten(value, (*prefix, name))
        else:
            keys.append((*prefix, name))
    return keys


def base_config(channel: str, port: int, fingerprint: str) -> Section:
    host = f"127.0.0.1:{port}"
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
                "port": str(port),
                "key": channel,
            },
            "trustedCertificates": {f'"{host}"': fingerprint},
        },
    }


def _quote(value: str) -> str:
    """configobj reads an unquoted comma as a list, and # as a comment."""
    if value and not any(c in value for c in ",#\"'") and value == value.strip():
        return value
    return f"'{value}'" if '"' in value else f'"{value}"'


def _render(section: Section, depth: int, lines: list[str]) -> None:
    for name, value in section.items():
        if not isinstance(value, dict):
            quoted = value if name.startswith('"') else _quote(value)
            lines.append(f"{name} = {quoted}")
    for name, value in section.items():
        if isinstance(value, dict):
            lines.append(f"{'[' * depth}{name}{']' * depth}")
            _render(value, depth + 1, lines)


def render(channel: str, port: int, fingerprint: str, settings: tuple[Setting, ...] = ()) -> str:
    config = base_config(channel, port, fingerprint)
    for setting in settings:
        section = config
        *sections, key = setting.path
        for name in sections:
            child = section.setdefault(name, {})
            if not isinstance(child, dict):
                raise BadSetting(".".join(setting.path), f"{name} is a key, not a section")
            section = child
        section[key] = setting.value
    lines: list[str] = []
    _render(config, 1, lines)
    return "\n".join(lines) + "\n"
