"""`asc keys`: search NVDA's own Commands Quick Reference (`keyCommands.html`).

The reference ships with every NVDA in `documentation\\<lang>\\`, so the keys
always match the NVDA in use. Tables are read by their header names (Name,
Desktop key, Laptop key, or a single Key column, and Description). Each row
remembers the heading it sits under, which searches also match.
"""

from __future__ import annotations

import os
import re
import winreg
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from agent_speech_check import winapi
from agent_speech_check.errors import NvdaNotFound

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# Single letter navigation is a list, not a table: "h: heading".
_LIST_ITEM = re.compile(r"([^:]{1,30}):\s+(.+)")
_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\NVDA"


BRAILLE_DISPLAYS = "SupportedBrailleDisplays"


@dataclass(frozen=True)
class Command:
    part: str  # the id of the <h2> it sits under, the same in every language
    section: str
    name: str
    desktop: str
    laptop: str
    description: str

    def key(self, layout: str) -> str:
        return self.laptop if layout == "laptop" else self.desktop

    def line(self, layout: str) -> str:
        described = f": {self.description}" if self.description else ""
        return f"{self.key(layout)}  {self.name}{described}"

    def matches(self, words: list[str]) -> bool:
        text = " ".join((self.section, self.name, self.desktop, self.laptop, self.description))
        lowered = text.lower()
        return all(word.lower() in lowered for word in words)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.commands: list[Command] = []
        self._section = ""
        self._part = ""
        self._text: list[str] | None = None
        self._headers: list[str] = []
        self._row: list[str] = []
        self._in_head = False
        # Text of the open <li>, or None. A list item with a link is a table
        # of contents entry, not a command.
        self._item: list[str] | None = None
        self._item_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h2":
            self._part = dict(attrs).get("id") or ""
        if tag in _HEADINGS or tag in ("td", "th"):
            self._text = []
        elif tag == "thead":
            self._in_head = True
            self._headers = []
        elif tag == "tr":
            self._row = []
        elif tag == "li":
            self._item, self._item_link = [], False
        elif tag == "a":
            self._item_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADINGS and self._text is not None:
            self._section = self._joined()
        elif tag == "th" and self._text is not None:
            self._headers.append(self._joined().lower())
        elif tag == "td" and self._text is not None:
            self._row.append(self._joined())
        elif tag == "thead":
            self._in_head = False
        elif tag == "tr" and not self._in_head and self._row:
            self._add_row()
        elif tag == "li" and self._item is not None:
            self._add_item(" ".join("".join(self._item).split()))
            self._item = None

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)
        if self._item is not None:
            self._item.append(data)

    def _add_item(self, text: str) -> None:
        match = _LIST_ITEM.fullmatch(text)
        if match and not self._item_link:
            key, name = match.groups()
            self.commands.append(Command(self._part, self._section, name, key, key, ""))

    def _joined(self) -> str:
        text = " ".join("".join(self._text or []).split())
        self._text = None
        return text

    def _cell(self, *names: str) -> str:
        for name in names:
            if name in self._headers:
                index = self._headers.index(name)
                if index < len(self._row):
                    return self._row[index]
        return ""

    def _add_row(self) -> None:
        name = self._cell("name")
        key = self._cell("key", "keys")
        desktop = self._cell("desktop key", "desktop keys") or key
        laptop = self._cell("laptop key", "laptop keys") or desktop
        if name and desktop:
            description = self._cell("description")
            self.commands.append(
                Command(self._part, self._section, name, desktop, laptop, description)
            )


def parse(html: str) -> list[Command]:
    parser = _Parser()
    parser.feed(html)
    parser.close()
    return parser.commands


def search(
    commands: list[Command], words: list[str], layout: str, *, braille: bool = False
) -> list[Command]:
    """Commands matching every word, with a key in `layout`."""
    return [
        c
        for c in commands
        if c.matches(words)
        and c.key(layout).lower() != "none"
        and (braille or c.part != BRAILLE_DISPLAYS)
    ]


# ---------------------------------------------------------------- finding NVDA


def _registry_dir() -> Path | None:
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, _UNINSTALL) as key:
                value, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if isinstance(value, str) and value:
            return Path(value)
    return None


def candidates() -> list[tuple[str, Path]]:
    """Where your own NVDA may be, in the order `asc keys` tries them."""
    found: list[tuple[str, Path]] = []
    running = winapi.process_image("nvda.exe")
    if running is not None:
        found.append(("the running nvda.exe", running.parent))
    registry = _registry_dir()
    if registry is not None:
        found.append(("the NVDA uninstall registry entry", registry))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            found.append((f"%{variable}%", Path(base) / "NVDA"))
    return found


def reference(nvda_dirs: list[tuple[str, Path]], lang: str) -> Path:
    """The first keyCommands.html in `lang`, falling back to English."""
    looked: list[str] = []
    for label, folder in nvda_dirs:
        for language in dict.fromkeys((lang, "en")):
            path = folder / "documentation" / language / "keyCommands.html"
            if path.is_file():
                return path
        looked.append(f"{label} ({folder})")
    raise NvdaNotFound(looked)
