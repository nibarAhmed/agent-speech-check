from dataclasses import replace
from pathlib import Path

import pytest

from agent_speech_check.errors import ModeMismatch, TargetsFileError, UsageError
from agent_speech_check.nvda_config import Setting
from agent_speech_check.targets import Target, defaults, load


def resolve(path: Path, name: str | None, mode: str | None) -> Target:
    return load(path).resolve(name, mode)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_no_file_gives_watch_default(tmp_path: Path) -> None:
    target = resolve(tmp_path / "config.toml", None, None)
    assert target == Target("default", "watch", 6837, keys=False, launch=False)


def test_mode_defaults() -> None:
    assert defaults("x", "desktop") == Target("x", "desktop", 0, keys=True, launch=True)
    assert defaults("x", "sandbox") == Target("x", "sandbox", 16837, keys=True, launch=True)


def test_listed_target_and_default_name(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        'default = "app"\n[targets.app]\nmode = "desktop"\nport = 16837\nlaunch = false\n',
    )
    target = resolve(path, None, None)
    assert target == Target("app", "desktop", 16837, keys=True, launch=False)
    assert resolve(path, "app", "desktop") == target
    assert resolve(path, "other", None).mode == "watch"


def test_mode_mismatch(tmp_path: Path) -> None:
    path = write(tmp_path, '[targets.app]\nmode = "watch"\n')
    with pytest.raises(ModeMismatch, match=r'target "app" is watch mode in config.toml'):
        resolve(path, "app", "desktop")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[targets.app]\nmode = "wach"\n', "mode: expected one of watch, desktop, sandbox, shared"),
        ('[targets.app]\nmode = "watch"\nkeys = true\n', "keys: expected false in watch mode"),
        ('[targets.app]\nmode = "watch"\nport = 0\n', "port: expected a whole number from 1"),
        ('[targets.app]\nmode = "watch"\nprot = 1\n', "expected only known keys, got prot"),
        ("[targets.app]\nmode = 'sandbox'\nshare = ['site']\n", "share: expected full paths"),
        ('default_target = "x"\n', "expected only known keys, got default_target"),
    ],
)
def test_bad_files(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(UsageError, match=message):
        resolve(write(tmp_path, text), "app", None)


def test_toml_syntax_error(tmp_path: Path) -> None:
    with pytest.raises(TargetsFileError, match=r"config.toml: "):
        resolve(write(tmp_path, "[targets\n"), None, None)


def test_bad_target_name(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="target name: expected 1 to 32 letters"):
        resolve(tmp_path / "none.toml", "bad name!", None)


def test_json_round_trip() -> None:
    target = defaults("x", "desktop")
    assert Target.from_json(target.to_json()) == target
    with pytest.raises(UsageError, match="daemon target: expected JSON"):
        Target.from_json("{")
    with pytest.raises(UsageError, match="keys: expected false in watch mode"):
        Target.from_json(
            '{"name":"x","mode":"watch","port":1,"keys":true,"launch":false,"settings":[]}'
        )
    with pytest.raises(UsageError, match="settings: expected a list of strings"):
        Target.from_json(
            '{"name":"x","mode":"desktop","port":1,"keys":true,"launch":true,"settings":[1]}'
        )


def test_json_round_trip_with_settings_and_nvda_dir() -> None:
    settings = (Setting.parse("speech.symbolLevel=300"), Setting.parse("presentation.x=a, b"))
    target = replace(defaults("x", "desktop"), nvda_dir="C:/nvda").with_settings(settings)
    assert Target.from_json(target.to_json()) == target


def test_nvda_dir_from_file(tmp_path: Path) -> None:
    path = write(tmp_path, "[targets.app]\nmode = 'desktop'\nnvda_dir = 'C:/nvda'\n")
    assert resolve(path, "app", None).nvda_dir == "C:/nvda"


def test_share_list(tmp_path: Path) -> None:
    path = write(tmp_path, "[targets.s]\nmode = 'sandbox'\nshare = ['C:/app', 'D:/data']\n")
    target = resolve(path, "s", None)
    assert target.share == ("C:/app", "D:/data")
    assert Target.from_json(target.to_json()) == target
    with pytest.raises(UsageError, match="share: expected a list of strings"):
        resolve(write(tmp_path, "[targets.s]\nmode = 'sandbox'\nshare = [1]\n"), "s", None)


@pytest.mark.parametrize(
    "table",
    [
        "mode = 'watch'",
        "mode = 'shared'",
        "mode = 'desktop'\nlaunch = false",
        "mode = 'sandbox'\nlaunch = false",
    ],
)
def test_nvda_dir_only_for_a_launched_nvda(tmp_path: Path, table: str) -> None:
    path = write(tmp_path, f"[targets.app]\n{table}\nnvda_dir = 'C:/nvda'\n")
    with pytest.raises(UsageError, match="only for an NVDA that asc launches") as exc:
        resolve(path, "app", None)
    assert exc.value.exit_code == 2


def test_nvda_dir_for_sandbox(tmp_path: Path) -> None:
    path = write(tmp_path, "[targets.app]\nmode = 'sandbox'\nnvda_dir = 'C:/nvda'\n")
    assert resolve(path, "app", None).nvda_dir == "C:/nvda"


def test_with_nvda_dir() -> None:
    target = defaults("x", "desktop")
    assert target.with_nvda_dir(None) is target
    assert Path(target.with_nvda_dir("C:/n").nvda_dir or "") == Path("C:/n")
    # The daemon runs in another folder, so a relative path is made absolute.
    assert target.with_nvda_dir("nvda").nvda_dir == str(Path.cwd() / "nvda")
    with pytest.raises(UsageError, match="only for an NVDA that asc launches"):
        defaults("x", "watch").with_nvda_dir("C:/n")


def test_relative_nvda_dir_in_file_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "[targets.app]\nmode = 'desktop'\nnvda_dir = 'nvda'\n")
    with pytest.raises(UsageError, match=r"a full path, like C:\\nvda, got") as exc:
        resolve(path, "app", None)
    assert exc.value.exit_code == 2


def test_window_defaults_to_tray_in_sandbox_mode(tmp_path: Path) -> None:
    path = write(tmp_path, "[targets.s]\nmode = 'sandbox'\n")
    target = resolve(path, "s", None)
    assert target.window is None
    assert target.sandbox_window == "tray"


@pytest.mark.parametrize("window", ["tray", "taskbar", "show"])
def test_window_from_file(tmp_path: Path, window: str) -> None:
    path = write(tmp_path, f"[targets.s]\nmode = 'sandbox'\nwindow = '{window}'\n")
    target = resolve(path, "s", None)
    assert target.sandbox_window == window
    assert Target.from_json(target.to_json()) == target


def test_bad_window(tmp_path: Path) -> None:
    path = write(tmp_path, "[targets.s]\nmode = 'sandbox'\nwindow = 'hidden'\n")
    with pytest.raises(UsageError, match="window: expected one of tray, taskbar, show") as exc:
        resolve(path, "s", None)
    assert exc.value.exit_code == 2


@pytest.mark.parametrize(
    "table", ["mode = 'watch'", "mode = 'desktop'", "mode = 'sandbox'\nlaunch = false"]
)
def test_window_only_for_a_sandbox_asc_launches(tmp_path: Path, table: str) -> None:
    path = write(tmp_path, f"[targets.app]\n{table}\nwindow = 'tray'\n")
    with pytest.raises(UsageError, match="window: expected only in sandbox mode") as exc:
        resolve(path, "app", None)
    assert exc.value.exit_code == 2


def test_with_window() -> None:
    target = defaults("x", "sandbox")
    assert target.with_window(None) is target
    assert target.with_window("taskbar").sandbox_window == "taskbar"
    with pytest.raises(UsageError, match="--window window: expected only in sandbox mode"):
        defaults("x", "desktop").with_window("show")


def test_file_not_in_utf8(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b'[targets.app]\nmode = "watch" # caf\xe9\n')
    with pytest.raises(TargetsFileError, match=r"config.toml: "):
        resolve(path, None, None)


def test_file_saved_with_a_bom(tmp_path: Path) -> None:
    """Some Windows editors start a UTF-8 file with a byte order mark."""
    path = tmp_path / "config.toml"
    path.write_bytes(b'\xef\xbb\xbf[targets.app]\nmode = "desktop"\n')
    assert resolve(path, "app", None).mode == "desktop"


def test_clash_in_kept_settings_names_the_file(tmp_path: Path) -> None:
    text = (
        '[targets.app]\nmode = "desktop"\n'
        'nvda_settings = ["speech.symbolLevel=1", "speech.symbolLevel.x=2"]\n'
    )
    with pytest.raises(UsageError, match=r"^config.toml: \[targets.app\] nvda_settings "):
        resolve(write(tmp_path, text), "app", None)
