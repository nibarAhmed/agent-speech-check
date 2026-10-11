"""The thin Windows shells, called for real where that is harmless.

Nothing here starts NVDA or a sandbox, or changes the firewall. Calls that
depend on the machine (Hyper-V, wsb) accept either documented outcome.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from agent_speech_check import firewall, nvda, sandbox, winapi
from agent_speech_check.errors import (
    DownloadFailed,
    NoDefaultSwitch,
    NvdaAlreadyRunning,
    NvdaNotFound,
    NvdaTooOld,
    SandboxFailed,
    SandboxNeedsPro,
    WsbMissing,
)
from agent_speech_check.nvda_config import Relay, Setting
from agent_speech_check.sandboxed import SandboxSession


def test_kill_on_close_job_kills_the_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = winapi.kill_on_close_job(child.pid)
        assert winapi.image_path(child.pid) is not None
        winapi.close_handle(job)
        assert child.wait(10) is not None
    finally:
        child.kill()


def test_process_lookups() -> None:
    me = winapi.image_path(os.getpid())
    assert me is not None
    assert me.suffix.lower() == ".exe"
    assert os.getpid() in winapi.process_ids(me.name)
    assert winapi.process_image(me.name) is not None
    assert winapi.process_ids("no-such-image-asc.exe") == []
    assert winapi.process_image("no-such-image-asc.exe") is None
    assert winapi.image_path(0) is None


def test_window_helpers_without_a_sandbox() -> None:
    assert winapi.windows_titled("no window has this title, asc") == []
    sandbox.keep_minimized()  # nothing to do, and it must not fail


def test_mutex_is_exclusive(tmp_path: Path) -> None:
    name = winapi.mutex_name(tmp_path, "t")
    first = winapi.take_mutex(name)
    assert first is not None
    assert winapi.take_mutex(name) is None
    winapi.close_handle(first)


def test_nvda_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert nvda.managed_dir() == tmp_path / "agent-speech-check" / "nvda" / "portable"
    assert nvda.folder_for(None) == nvda.managed_dir()
    assert nvda.folder_for("C:/x") == Path("C:/x")
    assert nvda.installer_path("2026.2").name == "nvda_2026.2.exe"
    assert nvda.installer_url("2026.2").startswith("https://download.nvaccess.org/releases/")
    with pytest.raises(NvdaNotFound) as exc:
        nvda.require_exe(nvda.managed_dir())
    assert exc.value.hint == "asc get-nvda"
    with pytest.raises(NvdaNotFound) as exc:
        nvda.require_exe(tmp_path)
    assert exc.value.hint == "check --nvda-dir or nvda_dir"
    (tmp_path / "nvda.exe").write_bytes(b"")
    assert nvda.require_exe(tmp_path) == tmp_path / "nvda.exe"


def test_starting_nvda_is_refused_while_one_runs(tmp_path: Path) -> None:
    (tmp_path / "nvda.exe").write_bytes(b"")
    if not winapi.process_ids("nvda.exe"):
        pytest.skip("needs an NVDA running on this machine")
    with pytest.raises(NvdaAlreadyRunning):
        nvda.refuse_if_running()
    with pytest.raises(NvdaAlreadyRunning):
        nvda.create_portable(tmp_path / "installer.exe", tmp_path / "portable")
    with pytest.raises(NvdaAlreadyRunning):
        nvda.launch(tmp_path, tmp_path / "cfg", Relay("127.0.0.1", 1, "k", "f"), ())


def test_firewall_reads_are_harmless() -> None:
    assert Path(firewall.daemon_program()).is_file()
    try:
        address = firewall.default_switch_address()
    except NoDefaultSwitch:
        return
    assert firewall.parse_address(address) == address
    # A port no rule names.
    assert firewall.rule_ok(1, firewall.daemon_program()) is False


def test_sandbox_availability() -> None:
    assert sandbox.edition_id()
    try:
        sandbox.require_available()
    except (SandboxNeedsPro, WsbMissing):
        return
    assert isinstance(sandbox.running(), list)


def test_sandbox_session_prepares_its_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    session = SandboxSession(
        tmp_path / "target",
        ("C:/src/app",),
        Relay("172.24.80.1", 16837, "key", "fp"),
        (Setting.parse("speech.symbolLevel=300"),),
    )
    config = session._prepare()
    folder = tmp_path / "target" / "sandbox"
    script = (folder / "in" / sandbox.SCRIPT).read_text(encoding="utf-8")
    assert "nvda_2026.2.exe" in script
    assert (folder / "in" / sandbox.LAUNCH_SCRIPT).read_text(encoding="utf-8") == sandbox.LAUNCH_PS1
    ini = (folder / "config" / "nvda.ini").read_text(encoding="utf-8")
    assert "host = 172.24.80.1:16837" in ini
    assert "symbolLevel = 300" in ini
    assert "<Networking>Enable</Networking>" in config
    assert str(folder / "config") in config
    assert session.state == "starting"


def test_sandbox_session_with_your_portable(tmp_path: Path) -> None:
    portable = tmp_path / "portable"
    (portable / "userConfig" / "addons" / "a").mkdir(parents=True)
    (portable / "userConfig" / "nvda.ini").write_text("[speech]\nrate = 40\n", encoding="utf-8")
    session = SandboxSession(tmp_path / "target", (), Relay("h", 1, "k", "f"), (), portable)
    config = session._prepare()
    folder = tmp_path / "target" / "sandbox"
    assert "robocopy" in (folder / "in" / sandbox.SCRIPT).read_text(encoding="utf-8")
    assert f"<HostFolder>{portable}</HostFolder>" in config
    assert (folder / "config" / "addons" / "a").is_dir()
    ini = (folder / "config" / "nvda.ini").read_text(encoding="utf-8").splitlines()
    assert "rate = 40" in ini
    assert "synth = silence" in ini


def test_sandbox_session_before_start() -> None:
    session = SandboxSession(Path("x"), (), Relay("h", 1, "k", "f"), ())
    with pytest.raises(SandboxFailed, match="not running yet"):
        asyncio.run(session.launch("x"))
    asyncio.run(session.stop())  # nothing started, nothing to stop
    assert session.sandbox_id is None


def test_wsb_errors_for_an_unknown_sandbox() -> None:
    try:
        sandbox.require_available()
    except (SandboxNeedsPro, WsbMissing):
        pytest.skip("needs Windows Sandbox")
    unknown = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(SandboxFailed, match="wsb exec failed"):
        sandbox.exec_as_user(unknown, "cmd /c exit 0")
    with pytest.raises(SandboxFailed, match="wsb stop failed"):
        sandbox.stop(unknown)
    with pytest.raises(SandboxFailed, match="wsb ip failed"):
        sandbox.ip_address(unknown)
    assert sandbox.wait_gone(0) in (True, False)


def test_running_nvda_stop_kills_its_job() -> None:
    # A stand-in child process: Running only needs a process and its job.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    running = nvda.Running(child, winapi.kill_on_close_job(child.pid))
    running.stop()
    assert child.poll() is not None


def test_download_reuses_an_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer = nvda.installer_path("2026.2")
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"already here")
    assert nvda.download("2026.2") == installer
    assert installer.read_bytes() == b"already here"


def test_global_hotkey_fires_and_is_exclusive() -> None:
    pressed = threading.Event()
    f23 = 0x86  # a key no keyboard has, so the test never steals a real shortcut
    modifiers = winapi.MOD_CONTROL | winapi.MOD_SHIFT | winapi.MOD_ALT
    hotkey = winapi.GlobalHotkey(modifiers, f23, pressed.set)
    assert hotkey.start()
    try:
        second = winapi.GlobalHotkey(modifiers, f23, pressed.set)
        assert not second.start()
        second.stop()
        hotkey.post(winapi.WM_HOTKEY)
        assert pressed.wait(5)
    finally:
        hotkey.stop()
    again = winapi.GlobalHotkey(modifiers, f23, pressed.set)
    assert again.start()
    again.stop()


def test_failed_download_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No such release: NV Access answers 404, or there is no network at all.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(DownloadFailed, match="cannot download NVDA"):
        nvda.download("0.0-asc-no-such-version")
    assert not nvda.installer_path("0.0-asc-no-such-version").exists()


def test_file_version(tmp_path: Path) -> None:
    kernel32 = Path(os.environ["SYSTEMROOT"]) / "System32" / "kernel32.dll"
    version = winapi.file_version(kernel32)
    assert version is not None
    assert version[0] == 10  # Windows 10 and 11 both report 10
    empty = tmp_path / "nvda.exe"
    empty.write_bytes(b"")
    assert winapi.file_version(empty) is None
    assert winapi.file_version(tmp_path / "missing.exe") is None


def test_nvda_version_check() -> None:
    nvda.check_version("C:/nvda", None)
    nvda.check_version("C:/nvda", (2025, 1))
    nvda.check_version("C:/nvda", (2026, 2))
    with pytest.raises(NvdaTooOld, match=r"C:/nvda is version 2024\.4") as exc:
        nvda.check_version("C:/nvda", (2024, 4))
    assert exc.value.exit_code == 3
