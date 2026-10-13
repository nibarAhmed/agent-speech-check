from pathlib import Path

import pytest

from agent_speech_check import __version__, errors
from agent_speech_check.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"asc {__version__}"


def test_help_lists_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "130  interrupted" in out
    for category in errors.CATEGORIES:
        assert f"\n  {category.exit_code}    " in out


def test_no_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: no command given\nhint: run asc --help\n"


def test_argparse_usage_error_passes_through() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--no-such-flag"])
    assert exc.value.code == 2


def test_unexpected_exception_is_internal_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASC_DEBUG", raising=False)
    # A non-string argument makes argparse itself raise TypeError: a genuine
    # unexpected exception, without mocking any of our code.
    assert main([123]) == 6  # type: ignore[list-item]
    err = capsys.readouterr().err
    assert err.startswith("internal error: TypeError: ")
    assert "logs: " in err
    assert "Traceback" not in err


def test_debug_shows_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASC_DEBUG", "1")
    assert main([123]) == 6  # type: ignore[list-item]
    assert "Traceback" in capsys.readouterr().err


class _PressesCtrlC:
    """An argv item that raises KeyboardInterrupt as soon as argparse looks at it."""

    def _interrupt(self, *_: object) -> bool:
        raise KeyboardInterrupt

    __eq__ = _interrupt
    __getitem__ = _interrupt
    __hash__ = None  # type: ignore[assignment]


def test_ctrl_c_exits_130() -> None:
    assert main([_PressesCtrlC()]) == 130  # type: ignore[list-item]


@pytest.mark.parametrize("version", ["2026.2&calc", "latest", "2026,2", ""])
def test_get_nvda_refuses_a_bad_version_before_anything(
    version: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The version goes into the download URL and a file name.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert main(["get-nvda", "--version", version]) == 2
    assert "--version" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())
