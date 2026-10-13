"""Sandbox mode end to end, run on the host (a sandbox can't run inside one).

Needs `asc sandbox-setup` done once, and the NVDA installer: set
ASC_E2E_SANDBOX=1 and ASC_E2E_INSTALLER to the installer (scripts/e2e_sandbox.py
downloads it to .e2e/in). Your own NVDA keeps running; the test checks that
its process is untouched.
"""

from __future__ import annotations

import os
import shutil
import signal
import time
from pathlib import Path

import pytest
from tests.integration.conftest import run_asc

from agent_speech_check import nvda, sandbox, state, winapi

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("ASC_E2E_SANDBOX") != "1", reason="needs ASC_E2E_SANDBOX=1"),
]

APP = """<!doctype html><html><head><title>asc app</title></head><body>
<h1>asc app</h1>
<button>OK</button>
<label>Name <input type="text"></label>
</body></html>
"""

SECOND = """<!doctype html><html><head><title>asc second</title></head><body>
<label>Name <input type="text"></label>
</body></html>
"""


def _with_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A state folder under `tmp_path` that already has the NVDA installer."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer = nvda.installer_path(nvda.VERSION)
    installer.parent.mkdir(parents=True)
    shutil.copy(os.environ["ASC_E2E_INSTALLER"], installer)
    return tmp_path / "agent-speech-check"


def test_sandbox_mode_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _with_installer(tmp_path, monkeypatch)
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.html").write_text(APP, encoding="utf-8")
    (root / "config.toml").write_text(
        f"[targets.sb]\nmode = 'sandbox'\nshare = ['{app.as_posix()}']\n", encoding="utf-8"
    )
    users_nvda = sorted(winapi.process_ids("nvda.exe"))

    started = run_asc("start", "--target", "sb", timeout=420)
    assert started.returncode == 0, started.stdout + started.stderr
    assert "nvda: connected" in started.stdout
    assert "sandbox: running" in started.stdout
    try:
        # The default, tray: the window is hidden, so none is visible.
        assert winapi.windows_titled(sandbox.WINDOW_TITLE) == []
        # And focus went back: the sandbox client doesn't keep it.
        client = winapi.process_ids("WindowsSandboxRemoteSession.exe")
        assert winapi.window_pid(winapi.foreground()) not in client
        page = sandbox.share_folder(str(app)) + "\\app.html"
        launched = run_asc("launch", f'mshta.exe "{page}"', "--title", "asc app", "--target", "sb")
        assert launched.returncode == 0, launched.stderr
        # The app's own window has focus, not a hidden helper window of it
        # (mshta's MainWindowHandle is one, which NVDA calls "unknown").
        title = run_asc("press", "NVDA+t", "--target", "sb")
        assert "asc app" in title.stdout, title.stdout
        ok = run_asc("press", "Tab", "--expect", "OK button", "--target", "sb")
        assert ok.returncode == 0, ok.stdout + ok.stderr
        field = run_asc("press", "Tab", "--settle", "800", "--target", "sb")
        assert "mode: focus" in field.stdout, field.stdout
        assert run_asc("type", "hi", "--target", "sb").returncode == 0
        line = run_asc("press", "NVDA+Up", "--expect", "hi", "--target", "sb")
        assert line.returncode == 0, line.stdout + line.stderr

        # After those keys Windows limits who takes the focus: a second app
        # must still come to the front where NVDA hears it.
        (app / "second.html").write_text(SECOND, encoding="utf-8")
        second = sandbox.share_folder(str(app)) + "\\second.html"
        again = run_asc(
            "launch", f'mshta.exe "{second}"', "--title", "asc second", "--timeout", "10000",
            "--target", "sb",
        )  # fmt: skip
        assert again.returncode == 0, again.stderr
    finally:
        stopped = run_asc("stop", "--target", "sb", timeout=120)
    assert stopped.returncode == 0
    assert sandbox.wait_gone(120)
    assert sorted(winapi.process_ids("nvda.exe")) == users_nvda
    time.sleep(1)


def test_sandbox_goes_when_its_daemon_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _with_installer(tmp_path, monkeypatch)
    (root / "config.toml").write_text("[targets.k]\nmode = 'sandbox'\n", encoding="utf-8")

    started = run_asc("start", "--target", "k", timeout=420)
    assert started.returncode == 0, started.stdout + started.stderr
    daemon_pid = state.read(root / "k", "k").pid
    try:
        assert sandbox.running()
        # A hard kill (TerminateProcess on Windows): the daemon gets no chance
        # to stop its sandbox.
        os.kill(daemon_pid, signal.SIGTERM)
        deadline = time.monotonic() + 120
        while sandbox.running():
            assert time.monotonic() < deadline, "the sandbox outlived its daemon"
            time.sleep(2)
    finally:
        for sandbox_id in sandbox.running():
            sandbox.stop(sandbox_id)
    assert sandbox.wait_gone(120)
    time.sleep(1)


def _portable_from_a_sandbox(installer: Path, out: Path) -> Path:
    """Make a portable NVDA with the real installer, inside a throwaway sandbox:
    on the host it would restart the user's NVDA."""
    config = sandbox.config_xml(
        [
            sandbox.Mapped(str(installer.parent), sandbox.NVDA_IN),
            sandbox.Mapped(str(out), r"C:\out", read_only=False),
        ],
        networking=False,
    )
    portable = out / "portable"
    with sandbox.SandboxWindow("tray", "asc e2e sandbox: show or hide") as window:
        _make_portable(config, window, installer, portable)
    assert sandbox.wait_gone(120)
    return portable


def _make_portable(
    config: str, window: sandbox.SandboxWindow, installer: Path, portable: Path
) -> None:
    sandbox_id = sandbox.start_connected(config, window, portable.parent / "guard.err")
    try:
        # Mute the sandbox first, as the real start script does: the
        # installer's NVDA plays its start sound otherwise.
        muted = sandbox.exec_as_user(sandbox_id, "cmd /c net stop audiosrv /y")
        assert muted == 0, f"net stop audiosrv exited {muted}"
        sandbox.exec_as_user(
            sandbox_id,
            f"cmd /c {sandbox.NVDA_IN}\\{installer.name} --create-portable-silent "
            r"--portable-path=C:\out\portable",
        )
        deadline = time.monotonic() + 180
        count = -1
        while not (portable / "nvda.exe").is_file() or count != len(list(portable.rglob("*"))):
            assert time.monotonic() < deadline, "no portable NVDA within 180 s"
            count = len(list(portable.rglob("*"))) if portable.exists() else -1
            time.sleep(3)
    finally:
        sandbox.stop(sandbox_id)


def test_sandbox_mode_with_your_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "agent-speech-check"
    root.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    portable = _portable_from_a_sandbox(Path(os.environ["ASC_E2E_INSTALLER"]), out)
    user_config = portable / "userConfig"
    user_config.mkdir(exist_ok=True)
    (user_config / "nvda.ini").write_text(
        "[speech]\nsymbolLevel = 300\nsynth = espeak\n", encoding="utf-8"
    )
    (user_config / "asc-marker.txt").write_text("x", encoding="utf-8")
    users_nvda = sorted(winapi.process_ids("nvda.exe"))

    started = run_asc(
        "start",
        "--mode",
        "sandbox",
        "--target",
        "own",
        "--nvda-dir",
        str(portable),
        timeout=420,
    )
    assert started.returncode == 0, started.stdout + started.stderr
    assert "nvda: connected" in started.stdout
    try:
        assert winapi.windows_titled(sandbox.WINDOW_TITLE) == []
        config = root / "own" / "sandbox" / "config"
        assert (config / "asc-marker.txt").is_file()
        # NVDA has rewritten the file by now, in its own layout.
        ini = [
            line.strip() for line in (config / "nvda.ini").read_text(encoding="utf-8").splitlines()
        ]
        assert "symbolLevel = 300" in ini
        assert "synth = silence" in ini
        title = run_asc("press", "NVDA+T", "--settle", "800", "--target", "own")
        assert title.returncode == 0, title.stderr
        assert title.stdout.startswith('said: "'), title.stdout
    finally:
        stopped = run_asc("stop", "--target", "own", timeout=120)
    assert stopped.returncode == 0
    assert sandbox.wait_gone(120)
    assert sorted(winapi.process_ids("nvda.exe")) == users_nvda
    assert (
        (user_config / "nvda.ini").read_text(encoding="utf-8").startswith("[speech]\nsymbolLevel")
    )
