from __future__ import annotations

from pathlib import Path

import pytest

from agent_speech_check.windows.paths import write_atomic


def test_write_atomic_writes_text_with_unix_newlines(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    write_atomic(path, "one\ntwo\n")
    assert path.read_bytes() == b"one\ntwo\n"


def test_write_atomic_writes_bytes(tmp_path: Path) -> None:
    path = tmp_path / "a.pem"
    write_atomic(path, b"\x00\r\n\xff")
    assert path.read_bytes() == b"\x00\r\n\xff"


def test_write_atomic_replaces_an_old_file(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("old", encoding="utf-8")
    write_atomic(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("old", encoding="utf-8")
    # A lone surrogate can't be encoded, so the write fails part way.
    with pytest.raises(UnicodeEncodeError):
        write_atomic(path, "new\udcff")
    assert path.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]
