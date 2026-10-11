import pytest

from agent_speech_check.errors import BadGesture, UnknownKey
from agent_speech_check.keys import parse_gesture, parse_gestures, parse_key


def test_nvda_key_is_extended_insert() -> None:
    key = parse_key("NVDA")
    assert (key.vk, key.extended) == (0x2D, True)


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("ctrl", "Control"),
        ("Esc", "Escape"),
        ("return", "Enter"),
        ("PgDn", "PageDown"),
        ("win", "Windows"),
        ("downarrow", "Down"),
        ("f12", "F12"),
        ("a", "A"),
        ("7", "7"),
    ],
)
def test_aliases_and_case(name: str, canonical: str) -> None:
    assert parse_key(name).name == canonical


def test_letters_digits_function_keys() -> None:
    assert parse_key("h").vk == ord("H")
    assert parse_key("0").vk == ord("0")
    assert parse_key("F1").vk == 0x70
    assert parse_key("F24").vk == 0x87


def test_numpad_with_num_lock_off() -> None:
    five = parse_key("Numpad5")
    assert (five.vk, five.extended) == (0x0C, False)
    assert parse_key("Numpad8").extended is False
    assert parse_key("Up").extended is True


def test_unknown_key_suggests() -> None:
    with pytest.raises(UnknownKey) as exc:
        parse_key("Escap")
    assert exc.value.message == 'unknown key "Escap"'
    assert exc.value.hint is not None
    assert "Escape" in exc.value.hint
    assert exc.value.exit_code == 2


def test_unknown_key_without_suggestions() -> None:
    with pytest.raises(UnknownKey) as exc:
        parse_key("qqqqqqqq")
    assert exc.value.hint == "see asc keys"


def test_gesture_order() -> None:
    gesture = parse_gesture("NVDA+Shift+Down")
    assert [k.name for k in gesture.strokes] == ["NVDA", "Shift", "Down"]
    assert str(gesture) == "NVDA+Shift+Down"
    assert str(parse_gesture("tab")) == "Tab"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("NVDA+", "empty key name"),
        ("+Tab", "empty key name"),
        ("Tab+A", "Tab is not a modifier"),
        ("Shift+Shift", "repeated"),
    ],
)
def test_bad_gestures(text: str, reason: str) -> None:
    with pytest.raises(BadGesture, match=reason):
        parse_gesture(text)


def test_all_parsed_before_any_used() -> None:
    with pytest.raises(UnknownKey):
        parse_gestures(["Tab", "Nope"])
    assert len(parse_gestures(["Tab", "H"])) == 2
