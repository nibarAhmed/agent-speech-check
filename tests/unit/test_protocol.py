"""Wire format tests. The sample lines are the shapes observed in docs/protocol.md."""

import json

import pytest

from agent_speech_check import protocol as p
from agent_speech_check.errors import LineTooLong, MalformedLine, ProtocolError, ServerSentError


def test_join_sequence_from_nvda() -> None:
    assert p.parse_message(b'{"type": "protocol_version", "version": 2}') == p.ProtocolVersion(2)
    assert p.parse_message(
        b'{"type": "join", "channel": "k1", "connection_type": "slave"}'
    ) == p.Join("k1", "slave")


def test_speak_keeps_strings_and_strips() -> None:
    line = json.dumps(
        {
            "type": "speak",
            "sequence": [
                ["LangChangeCommand", {"lang": None}],
                "Recycle Bin ",
                "list item ",
                "",
                "focused ",
                "1 of 2",
                ["EndUtteranceCommand", {}],
            ],
            "priority": 0,
        }
    ).encode()
    assert p.parse_message(line) == p.Speak("Recycle Bin list item focused 1 of 2", 0)


def test_speak_priority_optional() -> None:
    assert p.parse_message(b'{"type":"speak","sequence":["hi"]}') == p.Speak("hi", 0)


def test_speak_without_sequence_is_protocol_error() -> None:
    with pytest.raises(ProtocolError, match="NVDA speak message sequence: expected a list"):
        p.parse_message(b'{"type":"speak","priority":0}')


def test_wave_keeps_base_name() -> None:
    line = (
        b'{"type":"wave","fileName":"C:\\\\nvda\\\\waves\\\\focusMode.wav",'
        b'"asynchronous":true,"isSpeechWaveFileCommand":false}'
    )
    assert p.parse_message(line) == p.Wave("focusMode.wav")


def test_small_messages() -> None:
    assert p.parse_message(b'{"type":"cancel"}') == p.Cancel()
    assert p.parse_message(b'{"type":"ping"}') == p.Ping()
    assert p.parse_message(b'{"type":"pause_speech","switch":true}') == p.PauseSpeech(True)
    assert p.parse_message(b'{"type":"tone","hz":440,"length":50,"left":50,"right":50}') == p.Tone(
        440.0, 50
    )


def test_unknown_type_is_not_an_error() -> None:
    assert p.parse_message(b'{"type":"display","cells":[1]}') == p.Unknown("display")


def test_server_error_message() -> None:
    with pytest.raises(ServerSentError, match='NVDA reported an error: "incorrect_password"'):
        p.parse_message(b'{"type":"error","message":"incorrect_password"}')
    with pytest.raises(ServerSentError, match="no message"):
        p.parse_message(b'{"type":"error"}')


@pytest.mark.parametrize(
    "line",
    [b"not json", b"[1,2]", b'{"no_type":1}', b'{"type":5}', b"\xff\xfe"],
)
def test_malformed(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        p.parse_message(line)


def test_bad_json_names_reason() -> None:
    with pytest.raises(MalformedLine, match="not a JSON object: Expecting value"):
        p.parse_message(b"oops")


def test_oversize_line() -> None:
    with pytest.raises(LineTooLong):
        p.parse_message(b"x" * (p.MAX_LINE + 1))


def test_encoders() -> None:
    joined = json.loads(p.encode_channel_joined("k1"))
    assert joined == {
        "type": "channel_joined",
        "channel": "k1",
        "user_ids": [1],
        "clients": [{"id": 1, "connection_type": "master"}],
    }
    key = p.encode_key(45, extended=True, pressed=True)
    assert key.endswith(b"\n")
    assert json.loads(key) == {"type": "key", "vk_code": 45, "extended": True, "pressed": True}
    assert json.loads(p.encode_ping()) == {"type": "ping"}
    assert json.loads(p.encode_error("x")) == {"type": "error", "message": "x"}
