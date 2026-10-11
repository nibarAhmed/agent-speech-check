import pytest

from agent_speech_check.errors import BadSetting
from agent_speech_check.nvda_config import Setting, render


def test_base_config_matches_protocol_doc() -> None:
    ini = render("k1", 16837, "ab" * 32)
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
        f'"127.0.0.1:16837" = {"ab" * 32}',
    ]:
        assert expected in lines
    # configobj needs a section's keys before its subsections.
    assert lines.index("enabled = True") < lines.index("[[controlServer]]")


def test_user_settings_are_added_and_quoted() -> None:
    settings = (
        Setting.parse("speech.espeak.rate=50"),
        Setting.parse("presentation.reportTooltips = False"),
        Setting.parse("speech.symbolLevel=300"),
        Setting.parse('documentFormatting.note=a, b # "c"'),
    )
    lines = render("k", 1, "f", settings).splitlines()
    assert "[[espeak]]" in lines
    assert "rate = 50" in lines
    assert "[presentation]" in lines
    assert "reportTooltips = False" in lines
    assert "symbolLevel = 300" in lines
    assert "note = 'a, b # \"c\"'" in lines
    assert lines.index("symbolLevel = 300") < lines.index("[[espeak]]")


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
        render("k", 1, "f", (Setting(("speech", "synth", "x"), "1"),))
