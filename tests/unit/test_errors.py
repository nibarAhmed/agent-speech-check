import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from agent_speech_check import errors

CLI_DOC = Path(__file__).parents[2] / "docs" / "cli.md"


def test_exit_codes_and_kinds_are_unique() -> None:
    codes = [c.exit_code for c in errors.CATEGORIES]
    kinds = [c.kind for c in errors.CATEGORIES]
    assert len(set(codes)) == len(codes)
    assert len(set(kinds)) == len(kinds)


def test_cli_doc_exit_table_matches_errors() -> None:
    rows = re.findall(r"^\| (\d+) \|", CLI_DOC.read_text(encoding="utf-8"), re.MULTILINE)
    documented = {int(code) for code in rows}
    expected = {0, errors.EXIT_INTERRUPTED} | {c.exit_code for c in errors.CATEGORIES}
    assert documented == expected


@pytest.mark.parametrize("category", errors.CATEGORIES)
def test_from_kind_rebuilds_same_category(category: type[errors.AscError]) -> None:
    rebuilt = errors.from_kind(category.kind, "boom", "try this")
    assert type(rebuilt) is category
    assert rebuilt.message == "boom"
    assert rebuilt.hint == "try this"
    assert rebuilt.exit_code == category.exit_code


def test_from_kind_unknown_is_internal() -> None:
    rebuilt = errors.from_kind("nonsense", "boom")
    assert isinstance(rebuilt, errors.InternalError)
    assert "nonsense" in rebuilt.message


def _sample(annotation: str) -> object:
    if annotation.startswith("Sequence"):
        return ["x"]
    if "None" in annotation:
        return None
    return {"str": "x", "int": 1, "float": 1.5, "object": "x"}.get(annotation, "x")


def _concrete() -> list[type[errors.AscError]]:
    found = [
        cls
        for cls in vars(errors).values()
        if isinstance(cls, type)
        and issubclass(cls, errors.AscError)
        and cls not in (errors.AscError, *errors.CATEGORIES)
    ]
    assert len(found) > 30
    return found


@pytest.mark.parametrize("cls", _concrete(), ids=lambda c: c.__name__)
def test_every_error_builds_a_message(cls: type[errors.AscError]) -> None:
    params = list(inspect.signature(cls.__init__).parameters.values())[1:]
    args: list[Any] = [_sample(str(p.annotation)) for p in params]
    error = cls(*args)
    assert error.message
    assert error.kind in {c.kind for c in errors.CATEGORIES}
    assert error.exit_code == next(c for c in errors.CATEGORIES if isinstance(error, c)).exit_code
    assert chr(0x2014) not in error.message + (error.hint or "")


SETUP = (
    errors.NvdaNotFound,
    errors.NvdaTooOld,
    errors.PortableFailed,
    errors.NoDefaultSwitch,
    errors.FirewallRuleMissing,
    errors.FirewallFailed,
    errors.SandboxNeedsPro,
    errors.WsbMissing,
)


@pytest.mark.parametrize("cls", SETUP, ids=lambda c: c.__name__)
def test_missing_setup_exits_7_not_3(cls: type[errors.AscError]) -> None:
    # Exit 3 means retry or asc start; these need get-nvda, sandbox-setup or a person.
    assert cls.exit_code == 7


def test_busy_keyboard_is_refused_not_a_failed_expectation() -> None:
    # Exit 1 says the app spoke wrongly; a busy keyboard only means retry.
    assert errors.KeysBusy.exit_code == errors.Refused.exit_code


def test_failed_download_exits_3_so_agents_retry() -> None:
    # Usually the network; a retry often fixes it, so it is not a setup gap.
    assert errors.DownloadFailed.exit_code == errors.NotConnected.exit_code


def test_no_match_after_launch_says_which_windows_opened() -> None:
    # The agent can then tell a failed start from a window Windows kept behind.
    plain = errors.NoMatch("x", 5, [])
    assert plain.message.endswith("NVDA said:\n(nothing said)")
    opened = errors.NoMatch("x", 5, [], windows=["asc field"])
    assert opened.message.endswith('(nothing said)\nNew windows: "asc field"')
    assert errors.NoMatch("x", 5, [], windows=[]).message.endswith("\nNew windows: (none)")


def test_an_unreadable_user_config_names_the_line_when_known() -> None:
    assert errors.UserConfigUnreadable("nvda.ini", 3).message.endswith("nvda.ini line 3")
    assert errors.UserConfigUnreadable("nvda.ini", None).message.endswith("config: nvda.ini")
