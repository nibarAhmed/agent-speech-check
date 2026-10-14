import os
import shutil
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


def test_bad_arguments_are_a_short_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    # Like every other error: exit 2, one error line and a hint, no usage block.
    assert main(["press", "Tab", "--no-such-flag"]) == 2
    err = capsys.readouterr().err
    assert err == "error: unrecognized arguments: --no-such-flag\nhint: asc press --help\n"


def test_bad_arguments_without_a_command_point_at_asc_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--no-such-flag"]) == 2
    err = capsys.readouterr().err
    assert err == "error: unrecognized arguments: --no-such-flag\nhint: asc --help\n"


@pytest.mark.parametrize(
    ("argv", "hint"),
    [
        (["config", "path", "--no-such-flag"], "asc config path --help"),
        (["--no-such-flag", "status"], "asc --help"),
    ],
)
def test_bad_arguments_hint_names_the_parser_that_refused_them(
    capsys: pytest.CaptureFixture[str], argv: list[str], hint: str
) -> None:
    assert main(argv) == 2
    assert capsys.readouterr().err.endswith(f"\nhint: {hint}\n")


def test_bad_config_action_arguments_are_a_short_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["config"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: the following arguments are required: ACTION\n")
    assert err.endswith("hint: asc config --help\n")


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


class _FailsInATaskGroup:
    """An argv item that raises what a failed asyncio.TaskGroup raises."""

    def _fail(self, *_: object) -> bool:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("tray icon")])

    __eq__ = _fail
    __getitem__ = _fail
    __hash__ = None  # type: ignore[assignment]


def test_internal_error_in_a_group_names_the_inner_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The daemon's err file is all a user sees, so the real cause must be in it.
    monkeypatch.delenv("ASC_DEBUG", raising=False)
    assert main([_FailsInATaskGroup()]) == 6  # type: ignore[list-item]
    assert "internal error: ValueError: tray icon\n" in capsys.readouterr().err


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


def test_printed_paths_are_real(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store Python redirects %LOCALAPPDATA% for itself only, so a printed path
    must be the real one, or other programs won't find it. Without Store
    Python the paths are already real and this passes trivially."""
    root = Path(os.environ["LOCALAPPDATA"]) / f"asc-test-{os.getpid()}"
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    monkeypatch.delenv("ASC_DEBUG", raising=False)
    try:
        (root / "agent-speech-check").mkdir(parents=True)
        assert main(["config", "path"]) == 0
        printed = capsys.readouterr().out.strip()
        assert printed == os.path.realpath(printed)
        assert main([123]) == 6  # type: ignore[list-item]
        logs = capsys.readouterr().err.split("logs: ")[1].strip()
        assert logs == os.path.realpath(logs)
    finally:
        shutil.rmtree(os.path.realpath(root), ignore_errors=True)
