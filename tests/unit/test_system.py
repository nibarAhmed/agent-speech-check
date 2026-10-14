"""The thin Windows shells, called for real where that is harmless.

Nothing here starts NVDA or a sandbox, or changes the firewall. Calls that
depend on the machine (Hyper-V, wsb) accept either documented outcome.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agent_speech_check import firewall, ipc, nvda, sandbox, winapi
from agent_speech_check.errors import (
    DownloadFailed,
    NoDefaultSwitch,
    NvdaAlreadyRunning,
    NvdaNotFound,
    NvdaTooOld,
    SandboxBroken,
    SandboxFailed,
    SandboxNeedsPro,
    WsbMissing,
)
from agent_speech_check.nvda_config import RelayAddress, Setting
from agent_speech_check.sandboxed import SandboxSession
from agent_speech_check.session import sandbox_failure
from agent_speech_check.targets import defaults


def test_kill_on_close_job_kills_the_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = winapi.kill_on_close_job(child.pid)
        assert winapi.image_path(child.pid) is not None
        winapi.close_handle(job)
        assert child.wait(10) is not None
    finally:
        child.kill()


def test_terminate_a_process_that_ended_but_is_still_held() -> None:
    """While any handle to it is open, an ended process can still be opened,
    but not terminated. It is gone, so terminate has nothing to do."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(10)  # Popen keeps its handle open, so the pid stays openable.
    winapi.terminate(child.pid, 1)


def test_process_lookups() -> None:
    me = winapi.image_path(os.getpid())
    assert me is not None
    assert me.suffix.lower() == ".exe"
    assert os.getpid() in winapi.process_ids(me.name)
    assert winapi.process_image(me.name) is not None
    assert winapi.process_ids("no-such-image-asc.exe") == []
    assert winapi.process_image("no-such-image-asc.exe") is None
    assert winapi.image_path(0) is None


# Tests keep windows by this title, never the real one: a sandbox that another
# asc has open must not be moved by a test, nor make it skip.
TEST_WINDOW_TITLE = "asc test sandbox window"


@pytest.mark.xdist_group("desktop")
def test_window_helpers_without_a_sandbox() -> None:
    assert winapi.windows_titled("no window has this title, asc") == []
    assert sandbox.SandboxWindow("tray").title == sandbox.WINDOW_TITLE
    for window in ("tray", "taskbar", "show"):
        keeper = sandbox.SandboxWindow(window, title=TEST_WINDOW_TITLE)
        keeper.toggle()
        keeper.keep()  # nothing to do, and it must not fail
        assert keeper.shown
        keeper.remember_focus()
        with keeper:  # the keeping thread starts, runs and stops
            time.sleep(sandbox.KEEP_WINDOW_EVERY_S * 3)
        assert keeper.shown


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
        nvda.launch(tmp_path, tmp_path / "cfg", RelayAddress("127.0.0.1", 1, "k", "f"), ())


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
    target = replace(
        defaults("x", "sandbox"),
        share=("C:/src/app",),
        settings=(Setting.parse("speech.symbolLevel=300"),),
    )
    session = SandboxSession(
        tmp_path / "target", target, RelayAddress("172.24.80.1", 16837, "key", "fp")
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
    target = replace(defaults("x", "sandbox"), nvda_dir=str(portable))
    session = SandboxSession(tmp_path / "target", target, RelayAddress("h", 1, "k", "f"))
    config = session._prepare()
    folder = tmp_path / "target" / "sandbox"
    assert "robocopy" in (folder / "in" / sandbox.SCRIPT).read_text(encoding="utf-8")
    assert f"<HostFolder>{portable}</HostFolder>" in config
    assert (folder / "config" / "addons" / "a").is_dir()
    ini = (folder / "config" / "nvda.ini").read_text(encoding="utf-8").splitlines()
    assert "rate = 40" in ini
    assert "synth = silence" in ini


def test_sandbox_session_maps_real_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Store Python redirects writes under %LOCALAPPDATA% to its package's
    LocalCache, and wsb, outside the package, can't see the redirected path.
    Without Store Python the paths are already real and this passes trivially."""
    root = Path(os.environ["LOCALAPPDATA"]) / f"asc-test-{os.getpid()}"
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    try:
        state = root / "agent-speech-check"
        nvda.installer_path(nvda.VERSION).parent.mkdir(parents=True)
        session = SandboxSession(
            state / "x", defaults("x", "sandbox"), RelayAddress("h", 1, "k", "f")
        )
        config = session._prepare()
        hosts = [part.split("</HostFolder>")[0] for part in config.split("<HostFolder>")[1:]]
        assert len(hosts) == 3
        assert all(host == os.path.realpath(host) for host in hosts)
    finally:
        shutil.rmtree(os.path.realpath(root), ignore_errors=True)


def test_sandbox_failure_keeps_its_message_and_category() -> None:
    """The daemon reports why its sandbox failed; `asc start` raises that, unwrapped."""
    for error in (SandboxFailed("start", "file not found"), SandboxNeedsPro()):
        got = sandbox_failure({"sandbox_failure": ipc.error_detail(error)})
        assert got is not None
        assert (got.message, got.hint, got.exit_code) == (
            error.message,
            error.hint,
            error.exit_code,
        )
    assert sandbox_failure({"sandbox_failure": None}) is None


def test_sandbox_session_before_start() -> None:
    session = SandboxSession(Path("x"), defaults("x", "sandbox"), RelayAddress("h", 1, "k", "f"))
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
    # wsb's own errors have an unknown cause, so retrying won't help: exit 7.
    with pytest.raises(SandboxBroken, match="wsb exec failed") as broken:
        sandbox.exec_as_user(unknown, "cmd /c exit 0")
    assert broken.value.exit_code == 7
    with pytest.raises(SandboxBroken, match="wsb stop failed"):
        sandbox.stop(unknown)
    with pytest.raises(SandboxBroken, match="wsb ip failed"):
        sandbox.ip_address(unknown)
    assert sandbox.wait_gone(0) in (True, False)


def test_wait_exit_returns_once_the_process_ends() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    winapi.wait_exit(child.pid)
    assert child.poll() is not None
    winapi.wait_exit(child.pid)  # already gone: returns at once


def test_sandbox_guard_leaves_a_sandbox_that_is_gone(tmp_path: Path) -> None:
    try:
        sandbox.require_available()
    except (SandboxNeedsPro, WsbMissing):
        pytest.skip("needs Windows Sandbox")
    owner = subprocess.Popen([sys.executable, "-c", "pass"])
    owner.wait(10)
    unknown = "00000000-0000-0000-0000-000000000000"
    sandbox.guard(owner.pid, unknown)  # no `wsb stop` of an unknown id, so no error
    err = tmp_path / "guard.err"
    guard = sandbox.start_guard(owner.pid, unknown, err)
    assert guard.wait(60) == 0, err.read_text(encoding="utf-8")
    assert err.read_bytes() == b""


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


def test_a_new_installer_replaces_the_old_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    new = nvda.installer_path("2026.2")
    new.parent.mkdir(parents=True)
    for path in (new, nvda.installer_path("2025.1"), new.parent / "notes.txt"):
        path.write_bytes(b"")
    (new.parent / "portable").mkdir()
    nvda.drop_other_installers(new)
    assert sorted(p.name for p in new.parent.iterdir()) == ["notes.txt", new.name, "portable"]


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
    old = nvda.installer_path("2026.2")
    old.parent.mkdir(parents=True)
    old.write_bytes(b"")
    with pytest.raises(DownloadFailed, match="cannot download NVDA"):
        nvda.download("0.0-asc-no-such-version")
    assert not nvda.installer_path("0.0-asc-no-such-version").exists()
    assert old.exists()  # only a finished download replaces the old installers


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
    nvda.check_version("C:/nvda", (2026, 2))
    nvda.check_version("C:/nvda", (2027, 1))
    # 2025.1 has Remote Access but forwards no speech without an audio device.
    for old in ((2024, 4), (2025, 1), (2026, 1)):
        with pytest.raises(NvdaTooOld, match=r"C:/nvda is version \d+\.\d") as exc:
            nvda.check_version("C:/nvda", old)
        assert exc.value.exit_code == 7
        assert "needs 2026.2 or later" in exc.value.message


@pytest.mark.xdist_group("desktop")
def test_tray_icon_selects_and_goes_away() -> None:
    selected = threading.Event()
    icon = winapi.TrayIcon("asc test icon", selected.set)
    assert icon.start()
    try:
        # The window class is registered once per process; a second icon works too.
        second = winapi.TrayIcon("asc test icon 2", lambda: None)
        assert second.start()
        second.stop()
        icon.post(winapi.NIN_KEYSELECT)
        assert selected.wait(5)
    finally:
        icon.stop()
    selected.clear()
    icon.post(winapi.NIN_SELECT)  # gone: nothing to post to, nothing runs
    assert not selected.wait(0.2)


@pytest.mark.xdist_group("desktop")
def test_sandbox_session_tray_icon_only_in_tray_mode(tmp_path: Path) -> None:
    relay = RelayAddress("h", 1, "k", "f")
    tray = SandboxSession(tmp_path, defaults("x", "sandbox"), relay)
    tray.window.start()
    assert tray.window._icon is not None
    assert tray.window.window == "tray"
    asyncio.run(tray.stop())  # no sandbox started; the icon and the thread still go
    assert tray.window._icon is None
    assert not tray.window._thread.is_alive()
    for window in ("taskbar", "show"):
        other = SandboxSession(tmp_path, replace(defaults("x", "sandbox"), window=window), relay)
        other.window.start()
        assert other.window._icon is None
        other.window.stop()


def _plain_window(title: str) -> int:
    """A real top-level window, shown without taking focus."""
    user32 = ctypes.WinDLL("user32")
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.CreateWindowExW.argtypes += [ctypes.c_uint32] + [ctypes.c_int] * 4
    user32.CreateWindowExW.argtypes += [ctypes.c_void_p] * 4
    overlapped_window, show_no_activate = 0x00CF0000, 4
    hwnd = user32.CreateWindowExW(
        0, "STATIC", title, overlapped_window, 0, 0, 200, 100, None, None, None, None
    )
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow(hwnd, show_no_activate)
    return int(hwnd)


@pytest.mark.xdist_group("desktop")
def test_sandbox_window_puts_a_real_window_away() -> None:
    user32 = ctypes.WinDLL("user32")
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    for window, hidden in (("taskbar", False), ("tray", True)):
        hwnd = _plain_window(f"{TEST_WINDOW_TITLE} (asc test)")
        try:
            assert hwnd in winapi.windows_titled(TEST_WINDOW_TITLE)
            keeper = sandbox.SandboxWindow(window, title=TEST_WINDOW_TITLE)
            keeper.remember_focus()  # someone else's window: focus stays there
            keeper.keep()
            assert winapi.is_visible(hwnd) is not hidden
            assert winapi.is_minimized(hwnd)
            assert winapi.window_pid(hwnd) == os.getpid()
        finally:
            user32.DestroyWindow(hwnd)
