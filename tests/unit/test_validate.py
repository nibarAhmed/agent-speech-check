import pytest

from agent_speech_check.errors import ProtocolError, UsageError
from agent_speech_check.validate import Fields


def fields(raw: object, allowed: tuple[str, ...] | None = None) -> Fields:
    return Fields(raw, "targets.toml [targets.app]", UsageError, allowed)


def test_not_an_object() -> None:
    with pytest.raises(UsageError, match=r"\[targets.app\]: expected an object, got \[1\]"):
        fields([1])


def test_unknown_keys_listed() -> None:
    with pytest.raises(UsageError, match="expected only known keys, got modex, x"):
        fields({"modex": 1, "x": 2, "mode": 3}, ("mode",))


def test_text() -> None:
    f = fields({"a": "hi", "e": "", "n": 3})
    assert f.text("a") == "hi"
    assert f.text("e", allow_empty=True) == ""
    with pytest.raises(UsageError, match=r"app\] e: expected a string of 1 to 4096"):
        f.text("e")
    with pytest.raises(UsageError, match="got 3"):
        f.text("n")
    with pytest.raises(UsageError, match="got nothing"):
        f.text("missing")
    with pytest.raises(UsageError, match="1 to 1 characters"):
        f.text("a", max_len=1)
    assert f.opt_text("missing") is None
    assert f.opt_text("a") == "hi"


def test_number_rejects_bool_and_range() -> None:
    f = fields({"port": 70000, "ok": 80, "b": True})
    assert f.number("ok", low=1, high=65535) == 80
    with pytest.raises(
        UsageError, match="port: expected a whole number from 1 to 65535, got 70000"
    ):
        f.number("port", low=1, high=65535)
    with pytest.raises(UsageError, match="got true"):
        f.number("b", low=0, high=1)
    assert f.opt_number("missing", low=0, high=1) is None


def test_real_flag_choice_items() -> None:
    f = fields({"hz": 440, "on": False, "mode": "wach", "xs": [1], "s": "x"})
    assert f.real("hz") == 440.0
    with pytest.raises(UsageError, match="expected a number"):
        f.real("on")
    assert f.flag("on") is False
    with pytest.raises(UsageError, match="true or false"):
        f.flag("hz")
    with pytest.raises(UsageError, match='mode: expected one of watch, desktop, got "wach"'):
        f.choice("mode", ("watch", "desktop"))
    assert f.items("xs") == [1]
    with pytest.raises(UsageError, match="a list"):
        f.items("s")


def test_error_class_is_chosen_by_caller() -> None:
    with pytest.raises(ProtocolError):
        Fields("nope", "NVDA message", ProtocolError)


def test_long_values_are_shortened() -> None:
    with pytest.raises(UsageError) as exc:
        fields({"n": "x" * 500}).number("n", low=0, high=1)
    assert exc.value.message.endswith("xxx...")
    assert len(exc.value.message) < 200
