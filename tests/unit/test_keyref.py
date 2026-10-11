"""`asc keys` parsing, against NVDA's real Commands Quick Reference (tests/fixtures)."""

from pathlib import Path

import pytest

from agent_speech_check import keyref
from agent_speech_check.errors import NvdaNotFound

FIXTURE = Path(__file__).parents[1] / "fixtures" / "keyCommands.html"


@pytest.fixture(scope="module")
def commands() -> list[keyref.Command]:
    return keyref.parse(FIXTURE.read_text(encoding="utf-8"))


def lines(commands: list[keyref.Command], *words: str, layout: str = "desktop") -> list[str]:
    return [c.line(layout) for c in keyref.search(commands, list(words), layout)]


def test_table_row(commands: list[keyref.Command]) -> None:
    assert lines(commands, "report", "title") == [
        "NVDA+t  Report title: Reports the title of the currently active window. "
        "Pressing twice will spell the information. Pressing three times will copy it "
        "to the clipboard"
    ]


def test_layouts_differ(commands: list[keyref.Command]) -> None:
    desktop = lines(commands, "sleep mode", "toggle")
    laptop = lines(commands, "sleep mode", "toggle", layout="laptop")
    assert desktop[0].startswith("NVDA+shift+s  ")
    assert laptop[0].startswith("NVDA+shift+z  ")


def test_single_key_column_used_for_both_layouts(commands: list[keyref.Command]) -> None:
    date_time = keyref.search(commands, ["report date/time"], "laptop")
    assert date_time[0].laptop == date_time[0].desktop == "NVDA+f12"


def test_single_letter_navigation_list(commands: list[keyref.Command]) -> None:
    assert lines(commands, "single letter", "heading") == [
        "h  heading",
        "1 to 9  headings at levels 1 to 9 respectively",
    ]


def test_braille_displays_hidden_unless_asked(commands: list[keyref.Command]) -> None:
    hidden = keyref.search(commands, ["report title"], "desktop")
    shown = keyref.search(commands, ["report title"], "desktop", braille=True)
    assert len(shown) > len(hidden) == 1
    assert {c.part for c in shown} >= {keyref.BRAILLE_DISPLAYS}


def test_rows_without_a_key_are_skipped(commands: list[keyref.Command]) -> None:
    assert all(c.key("desktop").lower() != "none" for c in keyref.search(commands, [], "desktop"))


def test_matches_section_too(commands: list[keyref.Command]) -> None:
    found = keyref.search(commands, ["single letter navigation"], "desktop")
    assert len(found) > 20


def test_reference_falls_back_to_english(tmp_path: Path) -> None:
    english = tmp_path / "nvda" / "documentation" / "en" / "keyCommands.html"
    english.parent.mkdir(parents=True)
    english.write_text("<html></html>", encoding="utf-8")
    assert keyref.reference([("here", tmp_path / "nvda")], "de") == english


def test_reference_not_found_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(NvdaNotFound) as exc:
        keyref.reference([("the running nvda.exe", tmp_path)], "en")
    assert f"Looked in: the running nvda.exe ({tmp_path})" in exc.value.message
    assert exc.value.exit_code == 3


def test_candidates_include_program_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ProgramFiles", r"C:\PF")
    labels = [label for label, _ in keyref.candidates()]
    assert "%ProgramFiles%" in labels
