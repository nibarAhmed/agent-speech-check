from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from agent_speech_check import cli
from agent_speech_check.cli import COMMANDS, run
from agent_speech_check.config import prompts
from agent_speech_check.errors import OperationalError, UsageError
from agent_speech_check.update import uninstall


def _make_install(tmp_path: Path, version: str = "0.6.0") -> tuple[Path, Path, Path]:
    root = tmp_path / "home" / ".local" / "share" / "agent-speech-check"
    exe = root / "versions" / version / "asc"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"exe")
    exe.chmod(0o755)
    (root / "current").symlink_to(Path("versions") / version)
    link = tmp_path / "home" / ".local" / "bin" / "asc"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(root / "current" / "asc")
    return root, exe, link


def test_uninstall_is_refused_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert COMMANDS["uninstall"].linux_only
    with pytest.raises(UsageError, match="Linux installs") as exc:
        run(["uninstall"])
    assert "Settings > Apps" in (exc.value.hint or "")


def test_uninstall_refuses_non_tarball_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with pytest.raises(OperationalError, match="release tarball"):
        run(["uninstall", "--yes"])


def test_confirm_uninstall_prompts_and_handles_answers(tmp_path: Path) -> None:
    root = tmp_path / "agent-speech-check"
    exe = root / "versions" / "0.6.0" / "asc"
    link = tmp_path / "bin" / "asc"
    state = tmp_path / "state"
    with_state = uninstall.Plan(
        exe=exe,
        root=root,
        links=(link,),
        state=state,
        state_exists=True,
        has_orca_secret=False,
    )

    answers = iter(["n", "y"])
    assert prompts.confirm_uninstall(lambda _: next(answers), with_state, keep_data=False) is False

    answers = iter(["y", "y"])
    assert prompts.confirm_uninstall(lambda _: next(answers), with_state, keep_data=False) is True

    answers = iter(["y", "n"])
    assert prompts.confirm_uninstall(lambda _: next(answers), with_state, keep_data=False) is None

    asked: list[str] = []

    def ask_keep(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    assert prompts.confirm_uninstall(ask_keep, with_state, keep_data=True) is True
    assert len(asked) == 1

    without_state = uninstall.Plan(
        exe=exe,
        root=root,
        links=(link,),
        state=state,
        state_exists=False,
        has_orca_secret=False,
    )
    asked.clear()
    assert prompts.confirm_uninstall(ask_keep, without_state, keep_data=False) is False
    assert len(asked) == 1


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_find_links_finds_install_symlinks_and_ignores_unrelated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _exe, default_link = _make_install(tmp_path)
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    custom_link = custom_dir / "my-asc"
    custom_link.symlink_to(root / "current" / "asc")
    unrelated_link = custom_dir / "other-asc"
    unrelated_target = tmp_path / "elsewhere"
    unrelated_target.write_bytes(b"x")
    unrelated_link.symlink_to(unrelated_target)
    broken_link = custom_dir / "broken-asc"
    broken_link.symlink_to(tmp_path / "missing")
    regular_file = custom_dir / "plain-asc"
    regular_file.write_bytes(b"x")

    monkeypatch.setenv("PATH", str(custom_dir))
    found = uninstall.find_links(root, str(custom_link), default_link)
    assert found == (custom_link, default_link)
    assert uninstall.find_links(root, str(unrelated_link), default_link) == (default_link,)
    assert uninstall.find_links(root, str(broken_link), default_link) == (default_link,)
    assert uninstall.find_links(root, str(regular_file), default_link) == (default_link,)
    assert uninstall.find_links(root, str(root / "current"), default_link) == (default_link,)


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_plan_resolves_links_and_orca_secret(tmp_path: Path) -> None:
    root, exe, default_link = _make_install(tmp_path)
    state = tmp_path / "state" / "agent-speech-check"
    state.mkdir(parents=True)
    planned = uninstall.plan(exe, argv0=str(default_link), state=state, default_link=default_link)
    assert planned == uninstall.Plan(
        exe=exe,
        root=root,
        links=(default_link,),
        state=state,
        state_exists=True,
        has_orca_secret=False,
    )
    (state / "orca-secret").write_text("secret", encoding="utf-8")
    with_secret = uninstall.plan(
        exe, argv0=str(default_link), state=state, default_link=default_link
    )
    assert with_secret.has_orca_secret

    other = tmp_path / "other" / "versions" / "0.6.0" / "asc"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"exe")
    (tmp_path / "other" / "current").symlink_to(Path("versions", "0.6.0"))
    with pytest.raises(OperationalError, match="release tarball"):
        uninstall.plan(other, argv0="asc", state=state, default_link=default_link)


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_remove_files_when_the_state_is_the_install_folder(tmp_path: Path) -> None:
    root, exe, link = _make_install(tmp_path)
    same_state = uninstall.plan(exe, argv0=str(link), state=root, default_link=link)
    assert uninstall.remove_files(same_state, keep_data=False) == [link, root]
    assert not root.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_cmd_uninstall_requires_terminal_without_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, exe, link = _make_install(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "argv", [str(link), "uninstall"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert cli.main(["uninstall"]) == UsageError.exit_code
    assert "pass --yes" in capsys.readouterr().err
    assert root.exists()
    assert link.is_symlink()


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_cmd_uninstall_stops_daemons_removes_files_and_warns_about_orca_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, exe, link = _make_install(tmp_path)
    state_home = tmp_path / "state"
    state = state_home / "agent-speech-check"
    watch = state / "watch"
    watch.mkdir(parents=True)
    (state / "orca-secret").write_text("secret", encoding="utf-8")

    serve = (
        "import socket\n"
        "s = socket.create_server(('127.0.0.1', 0))\n"
        "print(s.getsockname()[1], flush=True)\n"
        "while True:\n"
        "    s.accept()[0].close()\n"
    )
    child = subprocess.Popen(  # noqa: S603 - our own interpreter
        [sys.executable, "-c", serve], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None
        port = int(child.stdout.readline())
        started = psutil.Process(child.pid).create_time()
        (watch / "state.json").write_text(
            json.dumps({"pid": child.pid, "port": port, "token": "t", "started": started}),
            encoding="utf-8",
        )

        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
        monkeypatch.setenv("PATH", str(link.parent))
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setattr(sys, "argv", [str(link), "uninstall", "--yes"])

        assert cli.main(["uninstall", "--yes"]) == 0
        assert child.wait(timeout=5) is not None
    finally:
        child.kill()
        child.wait()

    out = capsys.readouterr().out
    assert "stopped: watch" in out
    assert f"removed: {link}" in out
    assert f"removed: {state}" in out
    assert f"removed: {root}" in out
    assert "recording secret" in out
    assert not link.is_symlink()
    assert not root.exists()
    assert not state.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_cmd_uninstall_keep_data_preserves_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, exe, link = _make_install(tmp_path)
    state_home = tmp_path / "state"
    state = state_home / "agent-speech-check"
    state.mkdir(parents=True)
    (state / "config.toml").write_text("mode = 'desktop'\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("PATH", str(link.parent))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "argv", [str(link), "uninstall", "--keep-data", "--yes"])

    assert cli.main(["uninstall", "--keep-data", "--yes"]) == 0
    out = capsys.readouterr().out
    assert f"kept: {state}" in out
    assert not link.is_symlink()
    assert not root.exists()
    assert (state / "config.toml").is_file()
