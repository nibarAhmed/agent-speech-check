"""Sandbox pure parts. The JSON samples are real `wsb --raw` output (2026-10-11)."""

import base64

import pytest

from agent_speech_check import sandbox
from agent_speech_check.errors import NotConnected, SandboxFailed


def test_config_xml_escapes_and_maps() -> None:
    xml = sandbox.config_xml(
        [
            sandbox.Mapped("C:\\a&b", "C:\\in"),
            sandbox.Mapped("C:\\out", "C:\\out", read_only=False),
        ],
        networking=True,
        logon='cmd /c echo "<hi>"',
    )
    assert "<Networking>Enable</Networking>" in xml
    assert "<HostFolder>C:\\a&amp;b</HostFolder>" in xml
    assert "<ReadOnly>true</ReadOnly>" in xml
    assert "<ReadOnly>false</ReadOnly>" in xml
    assert '<Command>cmd /c echo "&lt;hi&gt;"</Command>' in xml
    assert "<ClipboardRedirection>Disable</ClipboardRedirection>" in xml


def test_config_without_network_or_logon() -> None:
    xml = sandbox.config_xml([], networking=False)
    assert "<Networking>Disable</Networking>" in xml
    assert "LogonCommand" not in xml


def test_parse_real_outputs() -> None:
    assert sandbox.parse_start('{\n  "Id": "3eaa4ef3-9bc7"\n}') == "3eaa4ef3-9bc7"
    ip = '{"Networks": [{"IpV4Address": "172.24.82.2"}]}'
    assert sandbox.parse_ip(ip) == "172.24.82.2"
    assert sandbox.parse_ip('{"Networks": []}') is None
    assert sandbox.parse_exec('{\n  "ExitCode": 0\n}') == 0
    listed = '{"WindowsSandboxEnvironments": [{"Id": "a"}, {"Id": "b"}]}'
    assert sandbox.parse_list(listed) == ["a", "b"]


def test_parse_bad_output() -> None:
    with pytest.raises(SandboxFailed, match="wsb start failed: Failed to start"):
        sandbox.parse_start("Failed to start process")
    with pytest.raises(NotConnected, match="wsb exec output ExitCode"):
        sandbox.parse_exec('{"ExitCode": "zero"}')


@pytest.mark.parametrize(
    ("edition", "home"),
    [
        ("Core", True),
        ("CoreSingleLanguage", True),
        ("Professional", False),
        ("Enterprise", False),
        ("Education", False),
    ],
)
def test_home_detection(edition: str, home: bool) -> None:
    assert sandbox.is_home_edition(edition) is home


def test_nvda_script() -> None:
    script = sandbox.nvda_script("nvda_2026.2.exe")
    lines = script.split("\r\n")
    assert lines[1] == "net stop audiosrv /y > nul 2>&1"
    create = r'"C:\asc\nvda\nvda_2026.2.exe" --create-portable-silent --portable-path=C:\nvda'
    assert create in lines
    assert lines[-2].startswith(r'start "" C:\nvda\nvda.exe --config-path=C:\asc\config ')


def test_share_folders() -> None:
    assert sandbox.share_folder("C:\\src\\app\\") == r"C:\share\app"
    assert sandbox.share_folder("D:/data/web") == r"C:\share\web"
    assert sandbox.share_folder("E:\\") == r"C:\share\E"
    folders = sandbox.session_folders("i", "n", "c", (r"C:\src\app",))
    assert [(m.sandbox, m.read_only) for m in folders] == [
        (r"C:\asc\in", True),
        (r"C:\asc\nvda", True),
        (r"C:\asc\config", False),
        (r"C:\share\app", True),
    ]


def test_launch_command_encodes_the_command() -> None:
    command = 'mshta.exe "C:/share/it\'s here/app.html"'
    line = sandbox.launch_command(command)
    prefix = "powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand "
    assert line.startswith(prefix)
    script = base64.b64decode(line.removeprefix(prefix)).decode("utf-16-le")
    assert script.endswith("-Command 'mshta.exe \"C:/share/it''s here/app.html\"'")
    assert "launch.ps1" in script
    assert "exit 3" in sandbox.LAUNCH_PS1


def test_nvda_script_from_a_portable() -> None:
    lines = sandbox.nvda_script(None).split("\r\n")
    assert r"robocopy C:\asc\nvda C:\nvda /e /njh /njs /nfl /ndl /np > nul" in lines
    assert not any("--create-portable-silent" in line for line in lines)
    assert lines[-2].startswith(r'start "" C:\nvda\nvda.exe --config-path=C:\asc\config ')


@pytest.mark.parametrize(
    ("window", "shown", "minimized", "step"),
    [
        # Tray: hidden unless the user showed it from the tray icon.
        ("tray", False, False, ("hide", False)),
        ("tray", False, True, ("hide", False)),
        ("tray", True, False, (None, True)),
        # Minimising a shown window sends it back to the tray.
        ("tray", True, True, ("hide", False)),
        ("taskbar", False, False, ("minimize", False)),
        ("taskbar", False, True, (None, False)),
        ("show", False, False, (None, False)),
        ("show", False, True, (None, False)),
    ],
)
def test_window_step(
    window: str, shown: bool, minimized: bool, step: tuple[str | None, bool]
) -> None:
    assert sandbox.window_step(window, shown=shown, minimized=minimized) == step
