"""The thin Windows shells, called for real where that is harmless.

Nothing here starts NVDA or a sandbox, or changes the firewall. Calls that
depend on the machine (Hyper-V, wsb) accept either documented outcome.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from agent_speech_check.config.targets import defaults
from agent_speech_check.daemon import ipc
from agent_speech_check.daemon.session import sandbox_failure
from agent_speech_check.errors import (
    DownloadFailed,
    InstallerInUse,
    InstallerTimedOut,
    NoDefaultSwitch,
    NvdaAlreadyRunning,
    NvdaNotFound,
    NvdaTooOld,
    PortableNotReplaced,
    SandboxBroken,
    SandboxFailed,
    SandboxNeedsPro,
    SetupNeeded,
    WsbMissing,
)
from agent_speech_check.nvda import launcher
from agent_speech_check.nvda.nvda_config import RelayAddress, Setting
from agent_speech_check.sandbox import firewall, wsb
from agent_speech_check.sandbox.sandboxed import SandboxSession
from agent_speech_check.windows import wait, winapi


def test_kill_on_close_job_kills_the_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = winapi.kill_on_close_job(child.pid)
        assert winapi.image_path(child.pid) is not None
        winapi.close_handle(job)
        assert child.wait(10) is not None
    finally:
        child.kill()


def test_kill_tree_ends_the_processes_it_started_too() -> None:
    sleeper = "import time; time.sleep(60)"
    start_child = f"subprocess.Popen([sys.executable, '-c', {sleeper!r}])"
    code = f"import subprocess, sys; {start_child}; {sleeper}"
    parent = subprocess.Popen([sys.executable, "-c", code])  # noqa: S603 - our own script
    try:
        # A venv's python.exe is a launcher with the real interpreter as its child,
        # so wait for the sleeper itself: a python with no children of its own.
        def sleeper_started() -> bool:
            family = psutil.Process(parent.pid).children(recursive=True)
            return any(not p.children() for p in family if p.name().startswith("python"))

        assert wait.poll_until(sleeper_started, 10, 0.05)
        family = psutil.Process(parent.pid).children(recursive=True)
        assert winapi.kill_tree(parent.pid) == []
        assert parent.wait(10) is not None
        assert not any(p.is_running() for p in family)
    finally:
        parent.kill()


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
    assert wsb.SandboxWindow("tray").title == wsb.WINDOW_TITLE
    for window in ("tray", "taskbar", "show"):
        keeper = wsb.SandboxWindow(window, title=TEST_WINDOW_TITLE)
        keeper.toggle()
        keeper.keep()  # nothing to do, and it must not fail
        assert keeper.shown
        keeper.remember_focus()
        with keeper:  # the keeping thread starts, runs and stops
            time.sleep(wsb.KEEP_WINDOW_EVERY_S * 3)
        assert keeper.shown


def test_mutex_is_exclusive(tmp_path: Path) -> None:
    name = winapi.mutex_name(tmp_path)
    first = winapi.take_mutex(name)
    assert first is not None
    assert winapi.take_mutex(name) is None
    winapi.close_handle(first)


def test_nvda_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.managed_dir() == tmp_path / "agent-speech-check" / "nvda" / "portable"
    assert launcher.folder_for(None) == launcher.managed_dir()
    assert launcher.folder_for("C:/x") == Path("C:/x")
    assert launcher.installer_path("2026.2").name == "nvda_2026.2.exe"
    assert launcher.installer_url("2026.2").startswith("https://download.nvaccess.org/releases/")
    with pytest.raises(NvdaNotFound) as exc:
        launcher.require_exe(launcher.managed_dir())
    assert exc.value.hint == "asc get-nvda"
    with pytest.raises(NvdaNotFound) as exc:
        launcher.require_exe(tmp_path)
    assert exc.value.hint == "check nvda_dir"
    (tmp_path / "nvda.exe").write_bytes(b"")
    assert launcher.require_exe(tmp_path) == tmp_path / "nvda.exe"


def test_starting_nvda_is_refused_while_one_runs(tmp_path: Path) -> None:
    (tmp_path / "nvda.exe").write_bytes(b"")
    if not winapi.process_ids("nvda.exe"):
        pytest.skip("needs an NVDA running on this machine")
    with pytest.raises(NvdaAlreadyRunning):
        launcher.refuse_if_running()
    with pytest.raises(NvdaAlreadyRunning):
        launcher.create_portable(tmp_path / "installer.exe", tmp_path / "portable")
    with pytest.raises(NvdaAlreadyRunning):
        launcher.launch(tmp_path, tmp_path / "cfg", RelayAddress("127.0.0.1", 1, "k", "f"), ())


def test_an_installer_that_hangs_is_ended_and_reported(tmp_path: Path) -> None:
    installer = tmp_path / "hangs.cmd"
    # Waits about 3 s, long past the timeout, and makes no portable copy.
    installer.write_text("@ping -n 4 127.0.0.1 > nul\r\n", encoding="utf-8")
    old = tmp_path / "portable" / "nvda.exe"
    old.parent.mkdir()
    old.write_bytes(b"the copy that works")
    with pytest.raises(InstallerTimedOut, match=r"within 0.5 s, so asc ended it"):
        launcher.run_installer(installer, tmp_path / "portable", timeout_s=0.5)
    # A failed update leaves the old copy to start with.
    assert old.read_bytes() == b"the copy that works"


def test_an_installer_that_is_not_a_program_needs_setup(tmp_path: Path) -> None:
    installer = tmp_path / "nvda_2026.2.exe"
    installer.write_text("not a program", encoding="utf-8")
    with pytest.raises(SetupNeeded) as exc:
        launcher.run_installer(installer, tmp_path / "portable", timeout_s=30)
    assert str(installer) in exc.value.message


# Makes the portable copy as the NVDA installer would. cmd splits
# --portable-path=DIR at the "=", so the folder arrives as %3.
FAKE_INSTALLER = '@mkdir "%3" && type nul > "%3\\nvda.exe"\r\n'


def test_a_new_portable_copy_replaces_the_old_one(tmp_path: Path) -> None:
    installer = tmp_path / "installer.cmd"
    installer.write_text(FAKE_INSTALLER, encoding="utf-8")
    dest = tmp_path / "portable"
    dest.mkdir()
    (dest / "nvda.exe").write_bytes(b"old")
    (dest / "only-old").write_bytes(b"")
    assert launcher.run_installer(installer, dest, timeout_s=30) == dest / "nvda.exe"
    assert (dest / "nvda.exe").read_bytes() == b""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["installer.cmd", "portable"]


def test_a_copy_left_mid_swap_is_put_back(tmp_path: Path) -> None:
    # A crash between the two renames leaves only portable.old.
    installer = tmp_path / "hangs.cmd"
    installer.write_text("@ping -n 4 127.0.0.1 > nul\r\n", encoding="utf-8")
    left = tmp_path / "portable.old" / "nvda.exe"
    left.parent.mkdir()
    left.write_bytes(b"the copy that works")
    with pytest.raises(InstallerTimedOut):
        launcher.run_installer(installer, tmp_path / "portable", timeout_s=0.5)
    assert (tmp_path / "portable" / "nvda.exe").read_bytes() == b"the copy that works"


def test_a_copy_in_use_is_not_replaced(tmp_path: Path) -> None:
    installer = tmp_path / "installer.cmd"
    installer.write_text(FAKE_INSTALLER, encoding="utf-8")
    dest = tmp_path / "portable"
    dest.mkdir()
    # An open file in the folder, as NVDA or an editor would hold, stops its rename.
    with (dest / "nvda.exe").open("wb") as held:
        held.write(b"old")
        held.flush()
        with pytest.raises(PortableNotReplaced, match="cannot replace the portable NVDA"):
            launcher.run_installer(installer, dest, timeout_s=30)
    assert (dest / "nvda.exe").read_bytes() == b"old"


def test_a_leftover_copy_in_use_is_reported(tmp_path: Path) -> None:
    installer = tmp_path / "installer.cmd"
    installer.write_text(FAKE_INSTALLER, encoding="utf-8")
    dest = tmp_path / "portable"
    (dest / "nvda.exe").parent.mkdir()
    (dest / "nvda.exe").write_bytes(b"old")
    # An earlier update could not delete portable.old because a file in it was open.
    stale = tmp_path / "portable.old" / "nvda.exe"
    stale.parent.mkdir()
    with (
        stale.open("wb"),
        pytest.raises(PortableNotReplaced, match="cannot replace the portable NVDA"),
    ):
        launcher.run_installer(installer, dest, timeout_s=30)
    assert (dest / "nvda.exe").read_bytes() == b"old"


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
    assert wsb.edition_id()
    try:
        wsb.require_available()
    except (SandboxNeedsPro, WsbMissing):
        return
    assert isinstance(wsb.running(), list)


def test_sandbox_session_prepares_its_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer = launcher.installer_path("2026.2")
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"")  # what the precheck downloaded
    target = replace(
        defaults("sandbox"),
        share=("C:/src/app",),
        settings=(Setting.parse("speech.symbolLevel=300"),),
    )
    session = SandboxSession(
        tmp_path / "target", target, RelayAddress("172.24.80.1", 16837, "key", "fp")
    )
    config = session._prepare()
    folder = tmp_path / "target" / "sandbox"
    script = (folder / "in" / wsb.SCRIPT).read_text(encoding="utf-8")
    assert "nvda_2026.2.exe" in script
    assert (folder / "in" / wsb.LAUNCH_SCRIPT).read_text(encoding="utf-8") == wsb.LAUNCH_PS1
    ini = (folder / "config" / "nvda.ini").read_text(encoding="utf-8")
    assert "host = 172.24.80.1:16837" in ini
    assert "symbolLevel = 300" in ini
    assert "<Networking>Enable</Networking>" in config
    assert str(folder / "config") in config
    assert session.state == "starting"


def test_sandbox_session_without_an_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "agent-speech-check" / "nvda").mkdir(parents=True)
    session = SandboxSession(tmp_path / "t", defaults("sandbox"), RelayAddress("h", 1, "k", "f"))
    with pytest.raises(NvdaNotFound, match="the NVDA installer"):
        session._prepare()


def test_sandbox_session_with_your_portable(tmp_path: Path) -> None:
    portable = tmp_path / "portable"
    (portable / "userConfig" / "addons" / "a").mkdir(parents=True)
    (portable / "userConfig" / "nvda.ini").write_text("[speech]\nrate = 40\n", encoding="utf-8")
    target = replace(defaults("sandbox"), nvda_dir=str(portable))
    session = SandboxSession(tmp_path / "target", target, RelayAddress("h", 1, "k", "f"))
    config = session._prepare()
    folder = tmp_path / "target" / "sandbox"
    assert "robocopy" in (folder / "in" / wsb.SCRIPT).read_text(encoding="utf-8")
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
        installer = launcher.installer_path("2026.2")
        installer.parent.mkdir(parents=True)
        installer.write_bytes(b"")
        session = SandboxSession(state / "x", defaults("sandbox"), RelayAddress("h", 1, "k", "f"))
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
    session = SandboxSession(Path("x"), defaults("sandbox"), RelayAddress("h", 1, "k", "f"))
    with pytest.raises(SandboxFailed, match="not running yet"):
        asyncio.run(session.launch("x"))
    asyncio.run(session.stop())  # nothing started, nothing to stop
    assert session.sandbox_id is None


def test_wsb_errors_for_an_unknown_sandbox() -> None:
    try:
        wsb.require_available()
    except (SandboxNeedsPro, WsbMissing):
        pytest.skip("needs Windows Sandbox")
    unknown = "00000000-0000-0000-0000-000000000000"
    # wsb's own errors have an unknown cause, so retrying won't help: exit 7.
    with pytest.raises(SandboxBroken, match="wsb exec failed") as broken:
        wsb.exec_as_user(unknown, "cmd /c exit 0")
    assert broken.value.exit_code == 7
    with pytest.raises(SandboxBroken, match="wsb stop failed"):
        wsb.stop(unknown)
    with pytest.raises(SandboxBroken, match="wsb ip failed"):
        wsb.ip_address(unknown)
    assert wsb.wait_gone(0) in (True, False)


def test_wait_exit_returns_once_the_process_ends() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    winapi.wait_exit(child.pid)
    assert child.poll() is not None
    winapi.wait_exit(child.pid)  # already gone: returns at once


def test_sandbox_guard_leaves_a_sandbox_that_is_gone(tmp_path: Path) -> None:
    try:
        wsb.require_available()
    except (SandboxNeedsPro, WsbMissing):
        pytest.skip("needs Windows Sandbox")
    owner = subprocess.Popen([sys.executable, "-c", "pass"])
    owner.wait(10)
    unknown = "00000000-0000-0000-0000-000000000000"
    wsb.guard(owner.pid, unknown)  # no `wsb stop` of an unknown id, so no error
    err = tmp_path / "guard.err"
    guard = wsb.start_guard(owner.pid, unknown, err)
    assert guard.wait(60) == 0, err.read_text(encoding="utf-8")
    assert err.read_bytes() == b""


def test_running_nvda_stop_kills_its_job() -> None:
    # A stand-in child process: Running only needs a process and its job.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    running = launcher.Running(child, winapi.kill_on_close_job(child.pid))
    running.stop()
    assert child.poll() is not None


def test_download_reuses_an_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer = launcher.installer_path("2026.2")
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"already here")
    stale = launcher.installer_path("2027.1")
    stale.write_bytes(b"")
    assert launcher.download("2026.2") == installer
    assert installer.read_bytes() == b"already here"
    # Else sandbox mode, which takes the highest cached version, would take it.
    assert not stale.exists()
    sha1 = "629d8889d129c48627f6629b2d0e640a9bb12541"  # of b"already here"
    assert launcher.download("2026.2", sha1) == installer


def test_a_damaged_cached_installer_is_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    damaged = launcher.installer_path("0.0-asc-no-such-version")
    damaged.parent.mkdir(parents=True)
    damaged.write_bytes(b"half a file")
    # Its SHA-1 doesn't match, so it goes; the download then fails, as there is no such release.
    with pytest.raises(DownloadFailed):
        launcher.download("0.0-asc-no-such-version", SHA1)
    assert not damaged.exists()


def test_a_held_damaged_installer_is_not_a_failed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Held open, as a sandbox running it would, so it can't be deleted. Not a
    # DownloadFailed, which the offline fallback catches and would return it.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    held = launcher.installer_path("2026.2")
    held.parent.mkdir(parents=True)
    with held.open("wb") as file:
        file.write(b"not the listed file")
        file.flush()
        with pytest.raises(InstallerInUse, match=r"nvda_2026\.2\.exe"):
            launcher.download("2026.2", SHA1)


def test_a_new_installer_replaces_the_old_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    new = launcher.installer_path("2026.2")
    new.parent.mkdir(parents=True)
    for path in (new, launcher.installer_path("2025.1"), new.parent / "notes.txt"):
        path.write_bytes(b"")
    (new.parent / "portable").mkdir()
    launcher.drop_other_installers(new)
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
    old = launcher.installer_path("2026.2")
    old.parent.mkdir(parents=True)
    old.write_bytes(b"")
    with pytest.raises(DownloadFailed, match="cannot download NVDA"):
        launcher.download("0.0-asc-no-such-version")
    assert not launcher.installer_path("0.0-asc-no-such-version").exists()
    assert old.exists()  # only a finished download replaces the old installers


# What api.nvaccess.org/nvdaUpdateCheck?versionType=stable answered on 2026-10-18.
NV_ACCESS_ANSWER = """\
apiCompatTo: 2026.1.0
apiVersion: 2026.2.0
launcherHash: a5b01592f4e2355432848d3ad82f5bd214f25947
launcherUrl: https://download.nvaccess.org/releases/2026.2/nvda_2026.2.exe
version: 2026.2
"""
SHA1 = "a5b01592f4e2355432848d3ad82f5bd214f25947"
# Nothing listens on port 1, so a lookup there fails at once, as offline.
NOWHERE = "http://127.0.0.1:1/"


def test_parse_release() -> None:
    assert launcher.parse_release(NV_ACCESS_ANSWER) == launcher.Release("2026.2", SHA1)
    assert launcher.parse_release(f"version: 2026.2.1\nlauncherHash: {SHA1.upper()}") == (
        launcher.Release("2026.2.1", SHA1)
    )
    # The version goes into a URL and a file name, so anything odd is no answer.
    for text in (
        "",
        "<html>maintenance</html>",
        f"version: 2026.2&calc\nlauncherHash: {SHA1}",
        "version: 2026.2\nlauncherHash: abc",
        f"launcherHash: {SHA1}",
    ):
        assert launcher.parse_release(text) is None


def test_newer() -> None:
    release = launcher.Release("2026.2", SHA1)
    assert launcher.newer(release, (2026, 1, 0))
    assert not launcher.newer(release, (2026, 2, 0))
    assert not launcher.newer(release, (2027, 1, 0))
    assert launcher.newer(launcher.Release("2026.2.1", SHA1), (2026, 2, 0))
    assert not launcher.newer(release, None)


def test_a_failed_lookup_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "UPDATE_URL", NOWHERE)
    with pytest.raises(DownloadFailed, match="cannot download NVDA"):
        launcher.latest()
    # A start never fails because NV Access can't be reached.
    kernel32 = Path(os.environ["SYSTEMROOT"]) / "System32" / "kernel32.dll"
    assert launcher.update_for(kernel32) is None


def test_no_update_for_a_file_without_a_version(tmp_path: Path) -> None:
    empty = tmp_path / "nvda.exe"
    empty.write_bytes(b"")
    assert launcher.update_for(empty) is None


def test_offline_the_cached_installer_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(launcher, "UPDATE_URL", NOWHERE)
    with pytest.raises(DownloadFailed):
        launcher.latest_installer()  # nothing cached to fall back on
    assert launcher.cached_installer() is None
    cached = launcher.installer_path("2026.1")
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"")
    assert launcher.cached_installer() == cached
    notes: list[str] = []
    assert launcher.latest_installer(notes.append) == cached
    # Said, so nobody takes the cached NVDA for the latest.
    assert notes == [f"cannot get the latest NVDA, so using {cached.name}"]


def test_offline_a_cached_installer_too_old_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(launcher, "UPDATE_URL", NOWHERE)
    cached = launcher.installer_path("2025.1")
    cached.parent.mkdir(parents=True)
    # Any file with a version resource older than MIN_VERSION will do.
    shutil.copy(Path(os.environ["SYSTEMROOT"]) / "System32" / "kernel32.dll", cached)
    with pytest.raises(NvdaTooOld) as exc:
        launcher.latest_installer()
    assert exc.value.hint == "connect to the network, or asc get-nvda"


def test_cached_installer_is_the_highest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    old, new = launcher.installer_path("2026.2"), launcher.installer_path("2026.10")
    old.parent.mkdir(parents=True)
    for path in (new, old, old.with_suffix(".part"), old.with_name("nvda_x.exe")):
        path.write_bytes(b"")
    assert launcher.cached_installer() == new


def test_a_garbled_answer_is_a_failed_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    # Like a captive portal: not HTTP at all, which http.client raises as no OSError.
    server = socket.create_server(("127.0.0.1", 0))

    def answer() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b"garbage\r\n\r\n")

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    monkeypatch.setattr(launcher, "UPDATE_URL", f"http://127.0.0.1:{server.getsockname()[1]}/")
    try:
        with pytest.raises(DownloadFailed):
            launcher.latest()
    finally:
        thread.join(5)
        server.close()


def test_the_lookup_has_one_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # A byte at a time, each sooner than the socket timeout, so only a limit on
    # the whole lookup stops it, as it must for slow name resolution.
    server = socket.create_server(("127.0.0.1", 0))
    done = threading.Event()

    def trickle() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            for byte in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 1000:
                if done.wait(0.1):  # the slowness is what this test is about
                    return
                conn.sendall(bytes([byte]))

    thread = threading.Thread(target=trickle, daemon=True)
    thread.start()
    monkeypatch.setattr(launcher, "UPDATE_URL", f"http://127.0.0.1:{server.getsockname()[1]}/")
    monkeypatch.setattr(launcher, "LOOKUP_TIMEOUT_S", 1)
    started = time.monotonic()
    try:
        with pytest.raises(DownloadFailed, match="no answer within 1 s"):
            launcher.latest()
        assert time.monotonic() - started < 3
    finally:
        done.set()
        thread.join(5)
        server.close()


def test_sha1_matches(tmp_path: Path) -> None:
    file = tmp_path / "nvda.exe"
    file.write_bytes(b"asc")
    assert launcher.sha1_matches(file, "43f0af343451be62bc8bdc64c292c7166d48491f")
    assert not launcher.sha1_matches(file, SHA1)


def test_file_version(tmp_path: Path) -> None:
    kernel32 = Path(os.environ["SYSTEMROOT"]) / "System32" / "kernel32.dll"
    version = winapi.file_version(kernel32)
    assert version is not None
    assert len(version) == 3
    assert version[0] == 10  # Windows 10 and 11 both report 10
    empty = tmp_path / "nvda.exe"
    empty.write_bytes(b"")
    assert winapi.file_version(empty) is None
    assert winapi.file_version(tmp_path / "missing.exe") is None


def test_nvda_version_check() -> None:
    named = Path("C:/nvda")
    launcher.check_version(named, None)
    launcher.check_version(named, (2026, 2))
    launcher.check_version(named, (2027, 1))
    # 2025.1 has Remote Access but forwards no speech without an audio device.
    for old in ((2024, 4), (2025, 1), (2026, 1)):
        with pytest.raises(NvdaTooOld, match=re.escape(f"{named} is version ") + r"\d+\.\d") as exc:
            launcher.check_version(named, old)
        assert exc.value.exit_code == 7
        assert "needs 2026.2 or later" in exc.value.message


def test_an_old_nvda_gets_the_hint_for_its_folder() -> None:
    # asc get-nvda can't replace an NVDA in a folder the user named in nvda_dir.
    with pytest.raises(NvdaTooOld) as exc:
        launcher.check_version(Path("C:/nvda"), (2025, 1))
    assert exc.value.hint == "check nvda_dir"
    with pytest.raises(NvdaTooOld) as exc:
        launcher.check_version(launcher.managed_dir(), (2025, 1))
    assert exc.value.hint == "asc get-nvda"


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
    tray = SandboxSession(tmp_path, defaults("sandbox"), relay)
    tray.window.start()
    assert tray.window._icon is not None
    assert tray.window.window == "tray"
    asyncio.run(tray.stop())  # no sandbox started; the icon and the thread still go
    assert tray.window._icon is None
    assert not tray.window._thread.is_alive()
    for window in ("taskbar", "show"):
        other = SandboxSession(tmp_path, replace(defaults("sandbox"), window=window), relay)
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
            keeper = wsb.SandboxWindow(window, title=TEST_WINDOW_TITLE)
            keeper.remember_focus()  # someone else's window: focus stays there
            keeper.keep()
            assert winapi.is_visible(hwnd) is not hidden
            assert winapi.is_minimized(hwnd)
            assert winapi.window_pid(hwnd) == os.getpid()
        finally:
            user32.DestroyWindow(hwnd)
