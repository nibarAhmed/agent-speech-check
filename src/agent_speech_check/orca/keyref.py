"""`asc keys` for Orca: its command pages (`commands_*.page`) in its own help.

The help is Mallard XML. A command is a list item `Name: keys`, or `Name:` over a
list of `Desktop: keys` and `Laptop: keys`. Keys are printed in asc's names.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

from agent_speech_check.errors import OrcaHelpNotFound, UnknownKey
from agent_speech_check.inputs import keys
from agent_speech_check.nvda.keyref import Command

_NS = "{http://projectmallard.org/1.0/}"
_CLICKS = {"(double-clicked)": "twice", "(triple-clicked)": "three times"}
_DATA_DIRS = os.pathsep.join(("/usr/local/share", "/usr/share"))


def pages(lang: str) -> list[Path]:
    """Orca's command pages in `lang`, else in English (`C`)."""
    looked: list[str] = []
    bases = (os.environ.get("XDG_DATA_DIRS") or _DATA_DIRS).split(os.pathsep)
    for language in dict.fromkeys((lang, "C")):
        for base in bases:
            folder = Path(base) / "help" / language / "orca"
            # Ubuntu links a language's pages into its language pack, which may be missing.
            found = sorted(p for p in folder.glob("commands_*.page") if p.is_file())
            if found:
                return found
            looked.append(str(folder))
    raise OrcaHelpNotFound(looked)


def parse(page: str) -> list[Command]:
    root = ElementTree.fromstring(page)  # noqa: S314 - Orca's own help, installed with it
    commands: list[Command] = []
    _walk(root, root.get("id", ""), _text(root.find(f"{_NS}title")), commands)
    return commands


def _walk(element: ElementTree.Element, part: str, section: str, out: list[Command]) -> None:
    for child in element:
        if child.tag == f"{_NS}section":
            _walk(child, part, _text(child.find(f"{_NS}title")), out)
        elif child.tag == f"{_NS}list":
            # A list of only Desktop: and Laptop: is the section's own command.
            _items(child, Command(part, section, section, "", "", ""), out)


def _items(parent: ElementTree.Element, command: Command, out: list[Command]) -> None:
    """`command` with the keys of the Desktop: and Laptop: items in `parent`'s list,
    then the other items as commands of their own."""
    layouts: dict[str, str] = {}
    others: list[ElementTree.Element] = []
    for item in parent.findall(f"{_NS}item"):
        layout, _, key = _text(item.find(f"{_NS}p")).partition(": ")
        if layout in ("Desktop", "Laptop"):
            layouts[layout.lower()] = _bound(key)
        else:
            others.append(item)
    desktop = layouts.get("desktop") or command.desktop
    if desktop:
        out.append(replace(command, desktop=desktop, laptop=layouts.get("laptop") or desktop))
    for item in others:
        _item(item, command.part, command.section, out)


def _item(item: ElementTree.Element, part: str, section: str, out: list[Command]) -> None:
    para = item.find(f"{_NS}p")
    name, _, key = _text(para).partition(":")
    has_key = para is not None and para.find(f".//{_NS}key") is not None
    command = Command(part, section, name.strip(), _bound(key) if has_key else "", "", "")
    sub = item.find(f"{_NS}list")
    if sub is not None:
        _items(sub, command, out)
    elif command.desktop:
        out.append(replace(command, laptop=command.desktop))


def _bound(key: str) -> str:
    key = key.strip().removesuffix(".")
    return "none" if key == "(Unbound)" else key


def _text(element: ElementTree.Element | None) -> str:
    """The element's text, its keys in asc's names, joined with + in a <keyseq>."""
    if element is None:
        return ""
    parts = [element.text or ""]
    for child in element:
        if child.tag == f"{_NS}keyseq":
            pressed = [_key(k.text or "") for k in child.iter(f"{_NS}key")]
            parts.append("+".join(pressed) if pressed else _key(child.text or ""))
        elif child.tag == f"{_NS}key":
            parts.append(_key(child.text or ""))
        elif child.tag != f"{_NS}list":
            parts.append(_text(child))
        parts.append(child.tail or "")
    text = " ".join("".join(parts).split())
    for clicks, times in _CLICKS.items():
        text = text.replace(clicks, times)
    return text


def _key(text: str) -> str:
    name = " ".join(text.split())
    if len(name) > 1:
        name = name.removesuffix(">")  # a typo in Orca's help: <key>Alt></key>
    if name == "Orca Modifier":
        return "Orca"
    if name.startswith("KP "):
        name = "Numpad" + name.removeprefix("KP ").replace("Subtract", "Minus")
    try:
        return keys.parse_key(name.replace(" ", "")).name
    except UnknownKey:
        return name
