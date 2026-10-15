from pathlib import Path

import pytest

from agent_speech_check.errors import BadSetting, UserConfigUnreadable
from agent_speech_check.nvda.nvda_config import (
    RelayAddress,
    Setting,
    check,
    parse_ini,
    render,
    source_config,
    user_ini,
    write,
)


def test_base_config_matches_protocol_doc() -> None:
    ini = render(RelayAddress("127.0.0.1", 16837, "k1", "ab" * 32))
    lines = ini.splitlines()
    for expected in [
        "[general]",
        "showWelcomeDialogAtStartup = False",
        "synth = silence",
        "soundVolumeFollowsVoice = False",
        "soundVolume = 0",
        "keyboardLayout = desktop",
        "passThroughAudioIndication = True",
        "[remote]",
        "enabled = True",
        "[[controlServer]]",
        "autoconnect = True",
        "connectionMode = 0",
        "host = 127.0.0.1:16837",
        "port = 16837",
        "key = k1",
        "[[trustedCertificates]]",
        f"127.0.0.1:16837 = {'ab' * 32}",
    ]:
        assert expected in lines
    remote = parse_ini(ini, "nvda.ini")["remote"]
    assert isinstance(remote, dict)
    assert remote["trustedCertificates"] == {"127.0.0.1:16837": "ab" * 32}
    # configobj needs a section's keys before its subsections.
    assert lines.index("enabled = True") < lines.index("[[controlServer]]")


def test_user_settings_are_added_and_quoted() -> None:
    settings = (
        Setting.parse("speech.espeak.rate=50"),
        Setting.parse("presentation.reportTooltips = False"),
        Setting.parse("speech.symbolLevel=300"),
        Setting.parse('documentFormatting.note=a, b # "c"'),
    )
    ini = render(RelayAddress("127.0.0.1", 1, "k", "f"), settings)
    lines = ini.splitlines()
    assert "[[espeak]]" in lines
    assert "rate = 50" in lines
    assert "[presentation]" in lines
    assert "reportTooltips = False" in lines
    assert "symbolLevel = 300" in lines
    assert "note = 'a, b # \"c\"'" in lines
    assert lines.index("symbolLevel = 300") < lines.index("[[espeak]]")
    assert parse_ini(ini, "nvda.ini")["documentFormatting"] == {"note": 'a, b # "c"'}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("speech", "expected section.key=value"),
        ("speech=1", "names are letters"),
        ("speech.1rate=1", "names are letters"),
        ("speech.rate=1\nx", "line breaks"),
        ("speech.rate=[x", r"start with \["),
        ("speech.rate=\"'", "both kinds of quote"),
        ("remote.controlServer.host=evil", "asc sets this itself"),
        ("Remote.anything=1", "asc sets this itself"),
        ("speech.synth=espeak", "asc sets this itself"),
        ("virtualBuffers.passThroughAudioIndication=False", "asc sets this itself"),
        ("audio.soundVolume=100", "asc sets this itself"),
        ("keyboard.keyboardLayout=laptop", "asc sets this itself"),
    ],
)
def test_bad_settings(text: str, reason: str) -> None:
    with pytest.raises(BadSetting, match=reason) as exc:
        Setting.parse(text)
    assert exc.value.exit_code == 2


def test_key_used_as_section() -> None:
    with pytest.raises(BadSetting, match="synth is a key, not a section"):
        render(RelayAddress("127.0.0.1", 1, "k", "f"), (Setting(("speech", "synth", "x"), "1"),))


def test_sandbox_relay_address_and_pinned_key() -> None:
    lines = render(RelayAddress("172.24.80.1", 16837, "k", "f")).splitlines()
    assert "host = 172.24.80.1:16837" in lines
    assert "172.24.80.1:16837 = f" in lines


USER_INI = """schemaVersion = 24
# a comment
[general]
language = de
showWelcomeDialogAtStartup = True
[speech]
synth = espeak
symbolLevel = 300
  # an indented comment

[[espeak]]
voice = de
rate = 70  # fast
[keyboard]
keyboardLayout = laptop
[remote]
enabled = True
[[controlServer]]
host = my.server:6837
key = secret
[[trustedCertificates]]
"my.server:6837" = cafe
[documentFormatting]
note = 'a, b'
voices = a, b
"""


def test_parse_ini_reads_values_as_nvda_does() -> None:
    parsed = parse_ini(USER_INI, "nvda.ini")
    assert parsed["schemaVersion"] == "24"
    speech = parsed["speech"]
    assert isinstance(speech, dict)
    assert speech["espeak"] == {"voice": "de", "rate": "70"}
    remote = parsed["remote"]
    assert isinstance(remote, dict)
    assert remote["trustedCertificates"] == {"my.server:6837": "cafe"}
    assert parsed["documentFormatting"] == {"note": "a, b", "voices": ["a", "b"]}


def test_a_multi_line_user_value_is_kept() -> None:
    user = parse_ini("[a]\nnote = '''x\ny'''\n", "nvda.ini")
    assert user == {"a": {"note": "x\ny"}}
    ini = render(RelayAddress("h", 1, "k", "f"), (), user)
    assert parse_ini(ini, "nvda.ini")["a"] == {"note": "x\ny"}


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("[a]\njust words\n", 2),
        ("[a]\nx = 1\nx = 2\n", 3),
        # Read, but configobj can't quote it to write it back.
        ("[a]\ny = 1\nx = a ''' \"\"\" b\n", 3),
        ("[a]\n[[[c]]]\n", 2),
        ("[a]]\n", 1),
        ("= x\n", 1),
    ],
)
def test_parse_ini_refuses_what_it_cannot_read(text: str, line: int) -> None:
    with pytest.raises(UserConfigUnreadable, match=f"nvda.ini line {line}") as exc:
        parse_ini(text, "nvda.ini")
    assert exc.value.exit_code == 2


def test_user_config_merge() -> None:
    settings = (Setting.parse("speech.symbolLevel=100"),)
    relay = RelayAddress("127.0.0.1", 16837, "k1", "ab" * 32)
    ini = render(relay, settings, parse_ini(USER_INI, "nvda.ini"))
    lines = ini.splitlines()
    # Kept from the user's config.
    assert "language = de" in lines
    assert "voice = de" in lines
    assert "rate = 70" in lines
    assert 'note = "a, b"' in lines
    assert "voices = a, b" in lines
    assert "schemaVersion = 24" in lines
    # asc's own settings win.
    assert "showWelcomeDialogAtStartup = False" in lines
    assert "synth = silence" in lines
    assert "keyboardLayout = desktop" in lines
    assert "symbolLevel = 100" in lines
    assert "symbolLevel = 300" not in lines
    # The user's Remote Access setup is dropped whole.
    assert "my.server" not in ini
    assert "secret" not in ini
    assert "host = 127.0.0.1:16837" in lines
    # configobj needs a section's keys before its subsections.
    assert lines.index("symbolLevel = 100") < lines.index("[[espeak]]")


def test_user_key_where_asc_needs_a_section() -> None:
    lines = render(RelayAddress("h", 1, "k", "f"), (), {"remote": "x", "speech": "y"})
    assert "[speech]" in lines.splitlines()
    assert "synth = silence" in lines.splitlines()


def test_settings_are_checked_against_the_user_config() -> None:
    user = parse_ini("[speech]\nvoice = de\n[[espeak]]\nrate = 1\n", "nvda.ini")
    check((Setting.parse("speech.voice.x=1"),))
    with pytest.raises(BadSetting, match="voice is a key"):
        check((Setting.parse("speech.voice.x=1"),), user)
    with pytest.raises(BadSetting, match="espeak is a section"):
        check((Setting.parse("speech.espeak=x"),), user)


def test_write_copies_the_user_config(tmp_path: Path) -> None:
    source = tmp_path / "userConfig"
    (source / "addons" / "a").mkdir(parents=True)
    (source / "addons" / "a" / "manifest.ini").write_text("name = a\n", encoding="utf-8")
    (source / "profiles").mkdir()
    (source / "profiles" / "p.ini").write_text("[speech]\nsynth = espeak\n", encoding="utf-8")
    (source / "profileTriggers.ini").write_text("[appProfiles]\n", encoding="utf-8")
    (source / "nvda.ini").write_text("\ufeff[speech]\nrate = 40\n", encoding="utf-8")
    config = tmp_path / "config"
    (config / "old").mkdir(parents=True)
    write(config, RelayAddress("h", 1, "k", "f"), (), source)
    assert (config / "addons" / "a" / "manifest.ini").is_file()
    assert (config / "profiles" / "p.ini").is_file()
    assert not (config / "profileTriggers.ini").exists()
    assert not (config / "old").exists()
    lines = (config / "nvda.ini").read_text(encoding="utf-8").splitlines()
    assert "rate = 40" in lines
    assert "synth = silence" in lines
    assert (source / "nvda.ini").read_text(encoding="utf-8") == "\ufeff[speech]\nrate = 40\n"


def test_write_without_a_user_config(tmp_path: Path) -> None:
    relay = RelayAddress("h", 1, "k", "f")
    for source in (None, tmp_path / "missing", tmp_path / "empty"):
        (tmp_path / "empty").mkdir(exist_ok=True)
        config = tmp_path / "config"
        write(config, relay, (), source)
        assert [p.name for p in config.iterdir()] == ["nvda.ini"]
        assert (config / "nvda.ini").read_text(encoding="utf-8") == render(relay)


def test_user_ini_is_checked(tmp_path: Path) -> None:
    assert user_ini(tmp_path) is None
    (tmp_path / "nvda.ini").write_text("[a]\nbroken\n", encoding="utf-8")
    with pytest.raises(UserConfigUnreadable, match="line 2"):
        user_ini(tmp_path)


def test_source_config_is_the_portable_user_config() -> None:
    assert source_config(Path("C:/nvda")) == Path("C:/nvda/userConfig")
