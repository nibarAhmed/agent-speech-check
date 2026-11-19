"""`asc keys` parsing, against NVDA's real Commands Quick Reference (tests/fixtures), and
against an Orca help page made up in its format (Orca's own pages are tested in e2e)."""

import os
from pathlib import Path

import pytest

from agent_speech_check.errors import FileUnusable, NvdaNotFound, OrcaHelpNotFound
from agent_speech_check.inputs.keyref import BRAILLE_DISPLAYS, Command, search
from agent_speech_check.nvda import keyref
from agent_speech_check.orca import helper
from agent_speech_check.orca import keyref as orca_keyref

FIXTURE = Path(__file__).parents[1] / "fixtures" / "keyCommands.html"


@pytest.fixture(scope="module")
def commands() -> list[Command]:
    return keyref.parse(FIXTURE.read_text(encoding="utf-8"))


def lines(commands: list[Command], *words: str, layout: str = "desktop") -> list[str]:
    return [c.line(layout) for c in search(commands, list(words), layout)]


def test_table_row(commands: list[Command]) -> None:
    assert lines(commands, "report", "title") == [
        "NVDA+t  Report title: Reports the title of the currently active window. "
        "Pressing twice will spell the information. Pressing three times will copy it "
        "to the clipboard"
    ]


def test_layouts_differ(commands: list[Command]) -> None:
    desktop = lines(commands, "sleep mode", "toggle")
    laptop = lines(commands, "sleep mode", "toggle", layout="laptop")
    assert desktop[0].startswith("NVDA+shift+s  ")
    assert laptop[0].startswith("NVDA+shift+z  ")


def test_single_key_column_used_for_both_layouts(commands: list[Command]) -> None:
    date_time = search(commands, ["report date/time"], "laptop")
    assert date_time[0].laptop == date_time[0].desktop == "NVDA+f12"


def test_single_letter_navigation_list(commands: list[Command]) -> None:
    assert lines(commands, "single letter", "heading") == [
        "h  heading",
        "1 to 9  headings at levels 1 to 9 respectively",
    ]


def test_braille_displays_hidden_unless_asked(commands: list[Command]) -> None:
    hidden = search(commands, ["report title"], "desktop")
    shown = search(commands, ["report title"], "desktop", braille=True)
    assert len(shown) > len(hidden) == 1
    assert {c.part for c in shown} >= {BRAILLE_DISPLAYS}


def test_rows_without_a_key_are_skipped(commands: list[Command]) -> None:
    assert all(c.key("desktop").lower() != "none" for c in search(commands, [], "desktop"))


def test_matches_section_too(commands: list[Command]) -> None:
    found = search(commands, ["single letter navigation"], "desktop")
    assert len(found) > 20


def test_reference_falls_back_to_english(tmp_path: Path) -> None:
    english = tmp_path / "nvda" / "documentation" / "en" / "keyCommands.html"
    english.parent.mkdir(parents=True)
    english.write_text("<html></html>", encoding="utf-8")
    assert keyref.reference([("here", tmp_path / "nvda")], "de", "hint") == english


def test_reference_not_found_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(NvdaNotFound) as exc:
        keyref.reference([("the running nvda.exe", tmp_path)], "en", "start your NVDA")
    assert f"Looked in: the running nvda.exe ({tmp_path})" in exc.value.message
    assert exc.value.hint == "start your NVDA"
    assert exc.value.exit_code == 7


ORCA_PAGE = """<page xmlns="http://projectmallard.org/1.0/" type="topic" id="commands_widgets">
  <info><title type="link">Widgets</title></info>
  <title>Widget Commands</title>
  <p>Not a command: <key>Q</key></p>
  <list>
    <item><p>Note: no key here</p></item>
    <item>
      <p>Toggle the <app>widget</app>:
        <keyseq><key>Orca Modifier</key><key>W</key></keyseq>.</p>
    </item>
    <item>
      <p>Spin the widget:</p>
      <list>
        <item><p>Desktop: <key>KP Subtract</key> (double-clicked)</p></item>
        <item>
          <p>Laptop: <keyseq><key>Orca Modifier</key><key>Right Bracket</key></keyseq></p>
        </item>
        <item><p>Polish the widget: (Unbound)</p></item>
      </list>
    </item>
  </list>
  <section id="gadgets">
    <title>Gadgets</title>
    <list>
      <item>
        <p>Next and previous gadget: <keyseq><key>G</key></keyseq> and
          <keyseq><key>Shift</key><key>G</key></keyseq></p>
      </item>
      <item>
        <p>Open the menu:
          <keyseq><key>Ctrl</key><key>Alt></key><key>Super</key><key>KP 5</key></keyseq></p>
      </item>
    </list>
  </section>
  <section id="whirl">
    <title>Whirl</title>
    <p>Whirls the gadget.</p>
    <list>
      <item><p>Desktop: <key>KP Plus</key></p></item>
      <item><p>Laptop: <keyseq><key>Orca Modifier</key><key>Semicolon</key></keyseq></p></item>
    </list>
  </section>
</page>
"""


def test_orca_page() -> None:
    commands = orca_keyref.parse(ORCA_PAGE)
    assert [c.line("desktop") for c in search(commands, [], "desktop")] == [
        "Orca+W  Toggle the widget",
        "NumpadMinus twice  Spin the widget",
        "G and Shift+G  Next and previous gadget",
        "Control+Alt+Windows+Numpad5  Open the menu",
        "NumpadPlus  Whirl",
    ]
    assert [c.line("laptop") for c in search(commands, [], "laptop")] == [
        "Orca+W  Toggle the widget",
        "Orca+RightBracket  Spin the widget",
        "G and Shift+G  Next and previous gadget",
        "Control+Alt+Windows+Numpad5  Open the menu",
        "Orca+Semicolon  Whirl",
    ]
    assert [c.name for c in search(commands, ["gadgets"], "desktop")] == [
        "Next and previous gadget",
        "Open the menu",
    ]
    assert all(c.part == "commands_widgets" for c in commands)


def test_orca_help_in_the_language_falls_back_to_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    help_dir = tmp_path / "share" / "help"
    for language in ("C", "fr"):
        (help_dir / language / "orca").mkdir(parents=True)
        (help_dir / language / "orca" / "commands_a.page").write_text("", encoding="utf-8")
    (help_dir / "C" / "orca" / "index.page").write_text("", encoding="utf-8")
    # As Ubuntu ships a language's help: links into a language pack that may not be there.
    (help_dir / "de" / "orca").mkdir(parents=True)
    (help_dir / "de" / "orca" / "commands_a.page").symlink_to("nowhere")
    # An empty entry is no folder, not the current one.
    monkeypatch.chdir(tmp_path / "share")
    monkeypatch.setenv(
        "XDG_DATA_DIRS", os.pathsep.join(("", str(tmp_path / "x"), str(tmp_path / "share")))
    )
    assert helper.help_pages("fr") == {str(help_dir / "fr" / "orca" / "commands_a.page"): ""}
    assert helper.help_pages("de") == {str(help_dir / "C" / "orca" / "commands_a.page"): ""}
    with pytest.raises(FileUnusable) as exc:
        orca_keyref.commands("fr", None)
    assert str(help_dir / "fr" / "orca" / "commands_a.page") in exc.value.message


def test_orca_help_not_found_says_where_it_looked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))
    with pytest.raises(OrcaHelpNotFound) as exc:
        orca_keyref.commands("en", None)
    assert str(tmp_path / "help" / "C" / "orca") in exc.value.message
    assert exc.value.exit_code == 7
