"""`config.toml`: how it is written, changed, and read back."""

from pathlib import Path
from typing import Any

import pytest

from agent_speech_check.config import edit, prefs
from agent_speech_check.config.targets import Config, load, parse
from agent_speech_check.errors import (
    BadPreference,
    BadSetting,
    SetupNeeded,
    UnknownPreference,
    UsageError,
)
from agent_speech_check.nvda.nvda_config import Setting, merge

EXAMPLE: dict[str, Any] = {
    "sandbox": {
        "nvda_settings": ["speech.symbolLevel=300"],
        "share": ["C:\\src\\app"],
    },
    "watch": {"port": 7000},
    "defaults": {"settle_ms": 800, "timeout_ms": 8000},
    "mode": "sandbox",
}

EXAMPLE_TEXT = """\
# asc settings. Change them with asc config set, or start over with asc setup.
mode = "sandbox"

[defaults]
settle_ms = 800
timeout_ms = 8000

[watch]
port = 7000

[sandbox]
share = ['C:\\src\\app']
nvda_settings = ["speech.symbolLevel=300"]
"""


def test_render_follows_the_conventions() -> None:
    # Header, top-level keys, [defaults], then a table per mode in MODES order, keys in
    # PREFS order, Windows paths as literal strings, LF line endings.
    assert edit.render(EXAMPLE) == EXAMPLE_TEXT


def test_render_reads_back() -> None:
    assert parse(edit.render(EXAMPLE), "x") == EXAMPLE


def test_path_with_a_quote_and_a_long_list_read_back() -> None:
    share = [f"C:\\it's here\\{'x' * 40}{n}" for n in range(3)]
    data = {"sandbox": {"share": share, "nvda_dir": "D:\\n v'da"}}
    text = edit.render(data)
    assert "share = [\n" in text  # too long for one line: one item per line
    assert parse(text, "x") == data


def test_save_checks_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"
    with pytest.raises(UsageError, match="settle_ms: expected a whole number from 50"):
        edit.save(path, {"defaults": {"settle_ms": 5}})
    assert not path.exists()
    edit.save(path, EXAMPLE)
    assert path.read_bytes() == EXAMPLE_TEXT.encode()
    assert load(path).modes["sandbox"].share == ("C:\\src\\app",)
    assert not list(path.parent.glob("*.tmp"))


def _config(data: dict[str, Any]) -> Config:
    return Config.from_data(data, "config.toml")


def test_mode_setting_goes_to_the_mode_settings_table() -> None:
    settings = _config({"mode": "desktop", "desktop": {"port": 7000}})
    pref = prefs.find("launch")
    where = edit.owner(settings, pref, None)
    assert where == "desktop"
    data, value = edit.set_value(settings, pref, ["False"], where)
    assert value is False
    assert data["desktop"] == {"port": 7000, "launch": False}
    assert settings.data["desktop"] == {"port": 7000}  # a copy was changed
    assert edit.owner(settings, pref, "sandbox") == "sandbox"


def test_any_mode_setting_makes_its_table() -> None:
    data, _ = edit.set_value(_config({}), prefs.find("port"), ["7000"], "shared")
    assert data == {"shared": {"port": 7000}}
    assert _config(data).resolve("shared").port == 7000
    data, _ = edit.set_value(_config({}), prefs.find("mode"), ["sandbox"], None)
    assert data == {"mode": "sandbox"}


def test_command_defaults_are_global_only() -> None:
    """Commands talk to whichever daemon runs, so no mode can hold their defaults."""
    settings = _config({"sandbox": {"window": "show"}})
    data, _ = edit.set_value(settings, prefs.find("settle_ms"), ["600"], None)
    assert data["defaults"] == {"settle_ms": 600}
    assert _config(data).value("settle_ms") == (600, "global")
    assert _config(data).value("timeout_ms") == (5000, "default")
    with pytest.raises(BadPreference, match="is global, so it takes no --mode"):
        edit.owner(settings, prefs.find("settle_ms"), "sandbox")


@pytest.mark.parametrize("name", ["panic_key", "mode"])
def test_global_setting_takes_no_mode(name: str) -> None:
    with pytest.raises(BadPreference, match="is global, so it takes no --mode"):
        edit.owner(_config({}), prefs.find(name), "watch")


def test_panic_key_is_kept_and_read_back_in_full() -> None:
    pref = prefs.find("panic_key")
    assert pref.parse(["ctrl+f11"]) == "Control+F11"
    with pytest.raises(BadPreference, match='"panic_key": bad gesture "F11": a hotkey needs'):
        pref.parse(["F11"])
    # Written by hand, it is read back in full too.
    config = _config({"defaults": {"panic_key": "ctrl+f11"}})
    assert config.value("panic_key") == ("Control+F11", "global")
    assert _config({}).value("panic_key") == ("Control+Shift+Alt+F12", "default")


def test_values_are_converted_by_type() -> None:
    pref = prefs.find("share")
    assert pref.parse(["a", "C:\\b"]) == [str(Path("a").absolute()), "C:\\b"]
    assert prefs.find("nvda_dir").parse(["n"]) == str(Path("n").absolute())
    assert prefs.find("port").parse(["7"]) == 7
    with pytest.raises(BadPreference, match="expected a whole number"):
        prefs.find("port").parse(["x"])
    with pytest.raises(BadPreference, match="expected true or false"):
        prefs.find("launch").parse(["yes"])
    with pytest.raises(BadPreference, match="takes one value"):
        prefs.find("mode").parse(["watch", "desktop"])


@pytest.mark.parametrize("name", ["share", "nvda_dir"])
def test_an_empty_path_is_not_the_current_folder(name: str) -> None:
    """`Path("").absolute()` is the current folder: an empty value would share it."""
    with pytest.raises(BadPreference, match="a path can't be empty"):
        prefs.find(name).parse([""])


def test_unknown_setting_suggests() -> None:
    with pytest.raises(UnknownPreference, match='unknown setting "setle_ms"') as exc:
        prefs.find("setle_ms")
    assert exc.value.hint == "did you mean settle_ms?"


def test_clashes_are_refused_when_checked() -> None:
    data, _ = edit.set_value(_config({}), prefs.find("window"), ["show"], "watch")
    with pytest.raises(UsageError, match="window: expected only in sandbox mode"):
        _config(data)


def test_unset() -> None:
    settings = _config({"defaults": {"settle_ms": 600}, "mode": "watch", "watch": {"port": 1}})
    data = edit.unset_value(settings, prefs.find("settle_ms"), None)
    assert data == {"mode": "watch", "watch": {"port": 1}}  # no empty [defaults] left
    data = edit.unset_value(_config(data), prefs.find("port"), "watch")
    assert data == {"mode": "watch"}  # nor an empty [watch]
    assert edit.unset_value(_config(data), prefs.find("mode"), None) == {}


def test_entries_say_where_each_value_comes_from() -> None:
    settings = _config(EXAMPLE)
    rows = {name: (value, source) for name, value, source in edit.entries(settings, None)}
    assert rows["mode"] == ("sandbox", "global")
    assert rows["port"] == (16837, "default")
    assert rows["nvda_settings"] == (["speech.symbolLevel=300"], "sandbox")
    assert rows["settle_ms"] == (800, "global")
    assert rows["timeout_ms"] == (8000, "global")
    assert rows["keys_layout"] == ("desktop", "default")
    watch = {name: (value, source) for name, value, source in edit.entries(settings, "watch")}
    assert watch["port"] == (7000, "watch")
    assert watch["mode"] == ("sandbox", "global")
    assert list(watch) == [p.name for p in prefs.PREFS]


@pytest.mark.parametrize(
    ("value", "shown"),
    [(None, "none"), (True, "true"), ([], "none"), (["a", "b"], "a, b"), (5, "5")],
)
def test_show(value: prefs.Value | None, shown: str) -> None:
    assert edit.show(value) == shown


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"defaults": {"mode": "watch"}}, "expected only known keys, got mode"),
        ({"defaults": {"keys_lang": "en"}}, "expected only known keys, got keys_lang"),
        ({"defaults": {"nvda_version": "2026.2"}}, "expected only known keys, got nvda_version"),
        ({"defaults": {"keys_layout": "tiny"}}, "keys_layout: expected one of desktop, laptop"),
        ({"mode": "vm"}, "mode: expected one of watch"),
        (
            {"watch": {"nvda_settings": ["speech.rate=5"]}},
            "nvda_settings: expected only for an NVDA that asc launches",
        ),
    ],
)
def test_bad_values_in_the_file(data: dict[str, Any], message: str) -> None:
    with pytest.raises(UsageError, match=message):
        _config(data)


def test_owned_nvda_setting_names_the_file() -> None:
    data = {"desktop": {"nvda_settings": ["speech.synth=x"]}}
    with pytest.raises(BadSetting, match=r"\[desktop\] nvda_settings \"speech.synth=x\""):
        _config(data)


def test_given_setting_replaces_a_kept_one() -> None:
    kept = (Setting.parse("speech.symbolLevel=300"), Setting.parse("speech.rate=5"))
    given = (Setting.parse("Speech.SymbolLevel=0"),)
    assert merge(kept, given) == (kept[1], given[0])


def test_save_into_a_folder_that_cannot_be_made_needs_setup(tmp_path: Path) -> None:
    (tmp_path / "asc").write_text("", encoding="utf-8")
    path = tmp_path / "asc" / "config.toml"
    with pytest.raises(SetupNeeded) as exc:
        edit.save(path, EXAMPLE)
    assert str(tmp_path / "asc") in exc.value.message


def test_save_does_not_trip_over_another_writers_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    (tmp_path / "config.toml.tmp").mkdir()
    edit.save(path, {"defaults": {"timeout_ms": 8000}})
    assert load(path).defaults == {"timeout_ms": 8000}
