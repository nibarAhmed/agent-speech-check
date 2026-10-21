from dataclasses import replace
from pathlib import Path

import pytest

from agent_speech_check.config.targets import Target, defaults, load
from agent_speech_check.errors import ConfigFileError, SetupNeeded, UsageError
from agent_speech_check.inputs.keys import PANIC_KEY, parse_hotkey
from agent_speech_check.nvda.nvda_config import Setting


def resolve(path: Path, mode: str | None) -> Target:
    return load(path).resolve(mode)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_no_file_gives_watch_default(tmp_path: Path) -> None:
    target = resolve(tmp_path / "config.toml", None)
    assert target == Target("watch", 6837, launch=False)


def test_mode_defaults() -> None:
    assert defaults("desktop") == Target("desktop", 0, launch=True)
    assert defaults("sandbox") == Target("sandbox", 16837, launch=True)


def test_keys_follow_the_mode() -> None:
    assert not defaults("watch").keys
    assert defaults("shared").keys
    assert defaults("sandbox").keys
    assert replace(defaults("desktop"), launch=False).keys


def test_panic_key_wherever_keys_reach_this_desktop() -> None:
    assert defaults("shared").takes_panic_key
    assert defaults("desktop").takes_panic_key
    # The sandbox's keys stay in the sandbox and watch sends none. launch = false
    # has none either: whoever starts that NVDA takes the risk (docs/cli.md).
    assert not replace(defaults("desktop"), launch=False).takes_panic_key
    assert not defaults("sandbox").takes_panic_key
    assert not defaults("watch").takes_panic_key


def test_mode_setting_and_its_table(tmp_path: Path) -> None:
    path = write(tmp_path, 'mode = "desktop"\n[desktop]\nport = 16837\nlaunch = false\n')
    target = resolve(path, None)
    assert target == Target("desktop", 16837, launch=False)
    assert resolve(path, "desktop") == target
    # Another mode, from --mode: its own table, or its defaults.
    assert resolve(path, "sandbox") == defaults("sandbox")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('mode = "wach"\n', "mode: expected one of watch, desktop, sandbox, shared"),
        ("[desktop]\nmode = 'desktop'\n", "expected only known keys, got mode"),
        ("[desktop]\nkeys = true\n", "expected only known keys, got keys"),
        ("[watch]\nport = 0\n", "port: expected a whole number from 1"),
        ("[watch]\nprot = 1\n", "expected only known keys, got prot"),
        ("[sandbox]\nshare = ['site']\n", "share: expected full paths"),
        ("[vm]\nport = 1\n", "expected only known keys, got vm"),
        ('default_target = "x"\n', "expected only known keys, got default_target"),
        (
            '[defaults]\npanic_key = "F12"\n',
            r'\[defaults\] panic_key: bad gesture "F12": a hotkey needs Control, Alt or Windows',
        ),
        ('[shared]\npanic_key = "Alt+F1"\n', "got panic_key"),
        ("[sandbox]\nsettle_ms = 900\n", "got settle_ms"),
    ],
)
def test_bad_files(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(UsageError, match=message):
        resolve(write(tmp_path, text), None)


def test_panic_key_default_and_from_file(tmp_path: Path) -> None:
    assert resolve(tmp_path / "none.toml", None).panic_key == parse_hotkey(PANIC_KEY)
    path = write(tmp_path, '[defaults]\npanic_key = "ctrl+alt+f11"\n[shared]\nport = 7000\n')
    target = resolve(path, "shared")
    assert target.panic_key.text == "Control+Alt+F11"
    # Global: every mode gets it, with a table or without.
    assert resolve(path, "watch").panic_key == target.panic_key
    assert Target.from_json(target.to_json()) == target


def test_toml_syntax_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match=r"config.toml: "):
        resolve(write(tmp_path, "[watch\n"), None)


def test_json_round_trip() -> None:
    target = defaults("desktop")
    assert Target.from_json(target.to_json()) == target
    with pytest.raises(UsageError, match="daemon target: expected JSON"):
        Target.from_json("{")
    with pytest.raises(UsageError, match="expected only known keys, got keys"):
        Target.from_json('{"mode":"watch","port":1,"keys":true,"launch":false,"settings":[]}')
    with pytest.raises(UsageError, match="expected only known keys, got name"):
        Target.from_json('{"name":"x","mode":"watch","port":1,"launch":false,"settings":[]}')
    with pytest.raises(UsageError, match="settings: expected a list of strings"):
        Target.from_json('{"mode":"desktop","port":1,"launch":true,"settings":[1]}')


def test_json_round_trip_with_settings_and_nvda_dir() -> None:
    settings = (Setting.parse("speech.symbolLevel=300"), Setting.parse("presentation.x=a, b"))
    target = replace(defaults("desktop"), nvda_dir="C:/nvda").with_settings(settings)
    assert Target.from_json(target.to_json()) == target


def test_nvda_dir_from_file(tmp_path: Path) -> None:
    path = write(tmp_path, "[desktop]\nnvda_dir = 'C:/nvda'\n")
    assert resolve(path, "desktop").nvda_dir == "C:/nvda"


def test_share_list(tmp_path: Path) -> None:
    path = write(tmp_path, "[sandbox]\nshare = ['C:/app', 'D:/data']\n")
    target = resolve(path, "sandbox")
    assert target.share == ("C:/app", "D:/data")
    assert Target.from_json(target.to_json()) == target
    with pytest.raises(UsageError, match="share: expected a list of strings"):
        resolve(write(tmp_path, "[sandbox]\nshare = [1]\n"), "sandbox")


@pytest.mark.parametrize(
    "table",
    ["[watch]", "[shared]", "[desktop]\nlaunch = false", "[sandbox]\nlaunch = false"],
)
def test_nvda_dir_only_for_a_launched_nvda(tmp_path: Path, table: str) -> None:
    path = write(tmp_path, f"{table}\nnvda_dir = 'C:/nvda'\n")
    with pytest.raises(UsageError, match="only for an NVDA that asc launches") as exc:
        resolve(path, None)
    assert exc.value.exit_code == 2


def test_nvda_dir_for_sandbox(tmp_path: Path) -> None:
    path = write(tmp_path, "[sandbox]\nnvda_dir = 'C:/nvda'\n")
    assert resolve(path, "sandbox").nvda_dir == "C:/nvda"


def test_relative_nvda_dir_in_file_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "[desktop]\nnvda_dir = 'nvda'\n")
    with pytest.raises(UsageError, match=r"a full path, like C:\\nvda, got") as exc:
        resolve(path, None)
    assert exc.value.exit_code == 2


def test_window_defaults_to_tray_in_sandbox_mode(tmp_path: Path) -> None:
    target = resolve(tmp_path / "none.toml", "sandbox")
    assert target.window is None
    assert target.sandbox_window == "tray"


@pytest.mark.parametrize("window", ["tray", "taskbar", "show"])
def test_window_from_file(tmp_path: Path, window: str) -> None:
    path = write(tmp_path, f"[sandbox]\nwindow = '{window}'\n")
    target = resolve(path, "sandbox")
    assert target.sandbox_window == window
    assert Target.from_json(target.to_json()) == target


def test_bad_window(tmp_path: Path) -> None:
    path = write(tmp_path, "[sandbox]\nwindow = 'hidden'\n")
    with pytest.raises(UsageError, match="window: expected one of tray, taskbar, show") as exc:
        resolve(path, None)
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("table", ["[watch]", "[desktop]", "[sandbox]\nlaunch = false"])
def test_window_only_for_a_sandbox_asc_launches(tmp_path: Path, table: str) -> None:
    path = write(tmp_path, f"{table}\nwindow = 'tray'\n")
    with pytest.raises(UsageError, match="window: expected only in sandbox mode") as exc:
        resolve(path, None)
    assert exc.value.exit_code == 2


def test_with_window() -> None:
    target = defaults("sandbox")
    assert target.with_window(None) is target
    assert target.with_window("taskbar").sandbox_window == "taskbar"
    with pytest.raises(UsageError, match="--window window: expected only in sandbox mode"):
        defaults("desktop").with_window("show")


def test_file_not_in_utf8(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b'mode = "watch" # caf\xe9\n')
    with pytest.raises(ConfigFileError, match=r"config.toml: "):
        resolve(path, None)


def test_file_the_disk_refuses_needs_setup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.mkdir()
    with pytest.raises(SetupNeeded, match=r"^cannot use .*config\.toml: "):
        load(path)


def test_file_saved_with_a_bom(tmp_path: Path) -> None:
    """Some Windows editors start a UTF-8 file with a byte order mark."""
    path = tmp_path / "config.toml"
    path.write_bytes(b'\xef\xbb\xbfmode = "desktop"\n')
    assert resolve(path, None).mode == "desktop"


def test_clash_in_kept_settings_names_the_file(tmp_path: Path) -> None:
    text = '[desktop]\nnvda_settings = ["speech.symbolLevel=1", "speech.symbolLevel.x=2"]\n'
    with pytest.raises(UsageError, match=r"^config.toml: \[desktop\] nvda_settings "):
        resolve(write(tmp_path, text), None)
