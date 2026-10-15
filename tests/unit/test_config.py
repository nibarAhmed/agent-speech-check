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
    TargetNotConfigured,
    UnknownPreference,
    UsageError,
)
from agent_speech_check.nvda.nvda_config import Setting, merge

EXAMPLE: dict[str, Any] = {
    "targets": {
        "default": {
            "settle_ms": 800,
            "mode": "sandbox",
            "nvda_settings": ["speech.symbolLevel=300"],
            "share": ["C:\\src\\app"],
        },
        "a": {"mode": "watch"},
    },
    "defaults": {"timeout_ms": 8000},
    "default": "default",
}

EXAMPLE_TEXT = """\
# asc settings. Change them with asc config set, or start over with asc setup.
default = "default"

[defaults]
timeout_ms = 8000

[targets.a]
mode = "watch"

[targets.default]
mode = "sandbox"
share = ['C:\\src\\app']
nvda_settings = ["speech.symbolLevel=300"]
settle_ms = 800
"""


def test_render_follows_the_conventions() -> None:
    # Header, top-level keys, [defaults], then targets by name, keys in PREFS order,
    # Windows paths as literal strings, LF line endings.
    assert edit.render(EXAMPLE) == EXAMPLE_TEXT


def test_render_reads_back() -> None:
    assert parse(edit.render(EXAMPLE), "x") == EXAMPLE


def test_path_with_a_quote_and_a_long_list_read_back() -> None:
    share = [f"C:\\it's here\\{'x' * 40}{n}" for n in range(3)]
    data = {"targets": {"s": {"mode": "sandbox", "share": share, "nvda_dir": "D:\\n v'da"}}}
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
    assert load(path).targets["default"].share == ("C:\\src\\app",)
    assert not list(path.parent.glob("*.tmp"))


def _config(data: dict[str, Any]) -> Config:
    return Config.from_data(data, "config.toml")


def test_set_target_setting_goes_to_the_default_target() -> None:
    settings = _config({"default": "app", "targets": {"app": {"mode": "desktop"}}})
    pref = prefs.find("launch")
    where = edit.owner(settings, pref, None)
    assert where == "app"
    data, value = edit.set_value(settings, pref, ["False"], where)
    assert value is False
    assert data["targets"]["app"] == {"mode": "desktop", "launch": False}
    assert settings.data["targets"]["app"] == {"mode": "desktop"}  # a copy was changed


def test_mode_makes_a_target_and_nothing_else_does() -> None:
    settings = _config({})
    with pytest.raises(TargetNotConfigured, match=r'target "app" is not in config.toml') as exc:
        edit.set_value(settings, prefs.find("port"), ["1"], "app")
    assert exc.value.hint == "asc config set mode MODE --target app"
    data, _ = edit.set_value(settings, prefs.find("mode"), ["sandbox"], "app")
    assert _config(data).targets["app"].mode == "sandbox"


def test_command_default_global_or_per_target() -> None:
    settings = _config({"targets": {"app": {"mode": "watch"}}})
    data, _ = edit.set_value(settings, prefs.find("settle_ms"), ["600"], None)
    assert data["defaults"] == {"settle_ms": 600}
    data, _ = edit.set_value(_config(data), prefs.find("settle_ms"), ["900"], "app")
    both = _config(data)
    assert both.value("settle_ms", "app") == (900, "target")
    assert both.value("settle_ms", "other") == (600, "global")
    assert both.value("timeout_ms", "app") == (5000, "default")


def test_global_setting_takes_no_target() -> None:
    with pytest.raises(BadPreference, match="is global, so it takes no --target"):
        edit.owner(_config({}), prefs.find("nvda_version"), "app")


def test_values_are_converted_by_type() -> None:
    pref = prefs.find("share")
    assert pref.parse(["a", "C:\\b"]) == [str(Path("a").absolute()), "C:\\b"]
    assert prefs.find("nvda_dir").parse(["n"]) == str(Path("n").absolute())
    assert prefs.find("port").parse(["7"]) == 7
    with pytest.raises(BadPreference, match="expected a whole number"):
        prefs.find("port").parse(["x"])
    with pytest.raises(BadPreference, match="expected true or false"):
        prefs.find("keys").parse(["yes"])
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
    settings = _config({"targets": {"app": {"mode": "desktop", "keys": True}}})
    data, _ = edit.set_value(settings, prefs.find("mode"), ["watch"], "app")
    with pytest.raises(UsageError, match="keys: expected false in watch mode"):
        _config(data)


def test_unset() -> None:
    settings = _config({"defaults": {"settle_ms": 600}, "targets": {"a": {"mode": "watch"}}})
    data = edit.unset_value(settings, prefs.find("settle_ms"), None)
    assert data == {"targets": {"a": {"mode": "watch"}}}  # no empty [defaults] left
    with pytest.raises(BadPreference, match="every target needs one"):
        edit.unset_value(settings, prefs.find("mode"), "a")


def test_remove_target() -> None:
    settings = _config({"targets": {"a": {"mode": "watch"}}})
    assert edit.remove_target(settings, "a") == {}
    with pytest.raises(TargetNotConfigured):
        edit.remove_target(settings, "b")


def test_entries_say_where_each_value_comes_from() -> None:
    settings = _config(EXAMPLE)
    rows = {name: (value, source) for name, value, source in edit.entries(settings, None)}
    assert rows["default"] == ("default", "global")
    assert rows["mode"] == ("sandbox", "target")
    assert rows["port"] == (16837, "default")
    assert rows["nvda_settings"] == (["speech.symbolLevel=300"], "target")
    assert rows["settle_ms"] == (800, "target")
    assert rows["timeout_ms"] == (8000, "global")
    assert rows["keys_braille"] == (False, "default")
    assert [name for name, _, _ in edit.entries(settings, "x")] == [p.name for p in prefs.PREFS]


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
        ({"defaults": {"keys_lang": "../x"}}, "keys_lang: expected a value like the default"),
        ({"defaults": {"keys_layout": "tiny"}}, "keys_layout: expected one of desktop, laptop"),
        ({"default": "bad name"}, "target name: expected 1 to 32"),
        (
            {"targets": {"a": {"mode": "watch", "nvda_settings": ["speech.rate=5"]}}},
            "nvda_settings: expected only for an NVDA that asc launches",
        ),
    ],
)
def test_bad_values_in_the_file(data: dict[str, Any], message: str) -> None:
    with pytest.raises(UsageError, match=message):
        _config(data)


def test_owned_nvda_setting_names_the_file() -> None:
    data = {"targets": {"a": {"mode": "desktop", "nvda_settings": ["speech.synth=x"]}}}
    with pytest.raises(BadSetting, match=r"\[targets.a\] nvda_settings \"speech.synth=x\""):
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
