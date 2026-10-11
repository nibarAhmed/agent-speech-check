import re
from pathlib import Path

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
