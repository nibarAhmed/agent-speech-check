"""`asc keys`: a screen reader's commands, as `nvda/keyref.py` and `orca/keyref.py`
read them from its own help, and the search over them."""

from __future__ import annotations

from dataclasses import dataclass

# The part of NVDA's reference that lists braille display keys. Orca's help has none.
BRAILLE_DISPLAYS = "SupportedBrailleDisplays"


@dataclass(frozen=True)
class Command:
    part: str  # the id of the page or <h2> it sits under, the same in every language
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
