"""The thin Windows shells, called for real where that is harmless.

Nothing here starts NVDA.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from agent_speech_check.config.targets import defaults
from agent_speech_check.daemon import ipc, session
from agent_speech_check.daemon.session import failure
from agent_speech_check.errors import (
    DownloadFailed,
    FileUnusable,
    OperationalError,
    UsageError,
)
from agent_speech_check.nvda import launcher
from agent_speech_check.nvda.nvda_config import RelayAddress
from agent_speech_check.system import powershell, processes, wait, winapi


def test_kill_on_close_job_kills_the_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = winapi.kill_on_close_job(child.pid)
        assert processes.image_path(child.pid) is not None
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
        assert processes.kill_tree(parent.pid) == []
        assert parent.wait(10) is not None
        assert not any(p.is_running() for p in family)
    finally:
        parent.kill()


def test_terminate_a_process_that_ended_but_is_still_held() -> None:
    """While any handle to it is open, an ended process can still be opened,
    but not terminated. It is gone, so terminate has nothing to do."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(10)  # Popen keeps its handle open, so the pid stays openable.
    processes.terminate(child.pid, 1)


def test_process_lookups() -> None:
    me = processes.image_path(os.getpid())
    assert me is not None
    assert me.suffix.lower() == ".exe"
    assert os.getpid() in processes.process_ids(me.name)
    assert processes.process_image(me.name) is not None
    assert processes.process_ids("no-such-image-asc.exe") == []
    assert processes.process_image("no-such-image-asc.exe") is None
    assert processes.image_path(0) is None


def test_powershell_quote_and_encoded() -> None:
    assert powershell.quote("C:\\it's\\x.exe") == "'C:\\it''s\\x.exe'"
    assert base64.b64decode(powershell.encoded("hi")).decode("utf-16-le") == "hi"


def test_mutex_is_exclusive(tmp_path: Path) -> None:
    name = winapi.mutex_name(tmp_path)
    first = winapi.take_mutex(name)
    assert first is not None
    assert winapi.take_mutex(name) is None
    winapi.close_handle(first)


def test_nvda_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.folder_for(None) == launcher.managed_dir()
    assert launcher.folder_for("C:/x") == Path("C:/x")
    with pytest.raises(OperationalError, match="NVDA not found") as exc:
        launcher.require_exe(launcher.managed_dir())
    assert exc.value.hint == "asc get-nvda"
    with pytest.raises(OperationalError, match="NVDA not found") as exc:
        launcher.require_exe(tmp_path)
    assert exc.value.hint == "check nvda_dir"
    (tmp_path / "nvda.exe").write_bytes(b"")
    assert launcher.require_exe(tmp_path) == tmp_path / "nvda.exe"


def test_starting_nvda_is_refused_while_one_runs(tmp_path: Path) -> None:
    (tmp_path / "nvda.exe").write_bytes(b"")
    if not processes.process_ids("nvda.exe"):
        pytest.skip("needs an NVDA running on this machine")
    with pytest.raises(OperationalError, match=r"nvda\.exe is already running"):
        launcher.refuse_if_running()
    with pytest.raises(OperationalError, match=r"nvda\.exe is already running"):
        launcher.create_portable(tmp_path / "installer.exe", tmp_path / "portable")
    with pytest.raises(OperationalError, match=r"nvda\.exe is already running"):
        launcher.launch(tmp_path, tmp_path / "cfg", RelayAddress("127.0.0.1", 1, "k", "f"), ())


def test_an_installer_that_hangs_is_ended_and_reported(tmp_path: Path) -> None:
    installer = tmp_path / "hangs.cmd"
    # Waits about 3 s, long past the timeout, and makes no portable copy.
    installer.write_text("@ping -n 4 127.0.0.1 > nul\r\n", encoding="utf-8")
    old = tmp_path / "portable" / "nvda.exe"
    old.parent.mkdir()
    old.write_bytes(b"the copy that works")
    with pytest.raises(OperationalError, match=r"within 0.5 s, so asc ended it"):
        launcher.run_installer(installer, tmp_path / "portable", timeout_s=0.5)
    # A failed update leaves the old copy to start with.
    assert old.read_bytes() == b"the copy that works"


def test_an_installer_that_is_not_a_program_fails(tmp_path: Path) -> None:
    installer = tmp_path / "nvda_2026.2.exe"
    installer.write_text("not a program", encoding="utf-8")
    with pytest.raises(OperationalError, match="cannot start ") as exc:
        launcher.run_installer(installer, tmp_path / "portable", timeout_s=30)
    assert str(installer) in exc.value.message
    assert "%1" not in exc.value.message


def test_an_installer_windows_refuses_to_open_is_not_called_damaged(tmp_path: Path) -> None:
    installer = tmp_path / "nvda_2026.2.exe"
    installer.mkdir()  # access denied, like a locked file: the program may be fine
    with pytest.raises(FileUnusable) as exc:
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
    with pytest.raises(OperationalError, match=r"did not finish within 0\.5 s"):
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
        with pytest.raises(OperationalError, match="cannot replace the portable NVDA"):
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
        pytest.raises(OperationalError, match="cannot replace the portable NVDA"),
    ):
        launcher.run_installer(installer, dest, timeout_s=30)
    assert (dest / "nvda.exe").read_bytes() == b"old"


def test_start_failure_keeps_its_message_and_category() -> None:
    """The daemon reports why Orca or the tunnel failed; `asc start` raises that, unwrapped."""
    for error in (
        OperationalError("busy", hint="close it"),
        UsageError("bad", hint="fix it"),
    ):
        got = failure({"failure": ipc.error_detail(error)})
        assert got is not None
        assert (got.message, got.hint, got.exit_code) == (
            error.message,
            error.hint,
            error.exit_code,
        )
    assert failure({"failure": None}) is None


def test_download_reuses_an_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer = launcher.installer_path("2026.2")
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"already here")
    stale = launcher.installer_path("2027.1")
    stale.write_bytes(b"")
    assert launcher.download("2026.2") == installer
    assert installer.read_bytes() == b"already here"
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
    # Held open, so it can't be deleted. Not a DownloadFailed, which the offline
    # fallback catches and would return it.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    held = launcher.installer_path("2026.2")
    held.parent.mkdir(parents=True)
    with held.open("wb") as file:
        file.write(b"not the listed file")
        file.flush()
        with pytest.raises(OperationalError, match=r"nvda_2026\.2\.exe") as exc:
            launcher.download("2026.2", SHA1)
        assert not isinstance(exc.value, DownloadFailed)


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
    with pytest.raises(OperationalError, match=r"needs 2026\.2 or later") as exc:
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
        with pytest.raises(
            OperationalError, match=re.escape(f"{named} is version ") + r"\d+\.\d"
        ) as exc:
            launcher.check_version(named, old)
        assert "needs 2026.2 or later" in exc.value.message
        assert exc.value.hint == "check nvda_dir"


def test_a_mode_folder_that_cannot_be_made_fails(tmp_path: Path) -> None:
    # Shared mode's relay files live in watch mode's folder, so its own is made at the spawn.
    blocked = tmp_path / "shared"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    with pytest.raises(FileUnusable) as exc:
        session._spawn(defaults("shared"), blocked)
    assert str(blocked) in exc.value.message
