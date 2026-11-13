from __future__ import annotations

from pathlib import Path

import pytest

from agent_speech_check.errors import BadInstance, SetupNeeded
from agent_speech_check.windows import paths
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


def test_a_target_the_disk_refuses_needs_setup_and_names_the_target(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.mkdir()
    with pytest.raises(SetupNeeded) as exc:
        write_atomic(path, "new")
    assert exc.value.message.startswith(f"cannot use {path}: ")
    assert ".tmp" not in exc.value.message
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_missing_folder_needs_setup_and_names_the_target(tmp_path: Path) -> None:
    path = tmp_path / "gone" / "a.txt"
    with pytest.raises(SetupNeeded) as exc:
        write_atomic(path, "new")
    assert exc.value.message.startswith(f"cannot use {path}: ")


def test_shared_mode_uses_watch_modes_relay_files() -> None:
    # Your own NVDA joins both, so it trusts one certificate and knows one key.
    assert paths.relay_dir("shared") == paths.relay_dir("watch") == paths.mode_dir("watch")
    for mode in ("desktop", "sandbox"):
        assert paths.relay_dir(mode) == paths.mode_dir(mode)


@pytest.mark.parametrize("name", ["vm1", "a", "agent-2", "x" * 32])
def test_instance_names(name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv(paths.INSTANCE_VAR, name)
    assert paths.state_root() == tmp_path / "agent-speech-check" / "instances" / name
    # The NVDA asc downloads is shared by every instance.
    assert paths.base_root() == tmp_path / "agent-speech-check"


@pytest.mark.parametrize(
    "name", ["-a", "VM1", "a_b", "..", r"a\b", "a/b", "x" * 33, "con", "nul", "com1", "lpt9"]
)
def test_bad_instance_names(name: str) -> None:
    with pytest.raises(BadInstance):
        paths.check_instance(name)
