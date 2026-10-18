"""Real NVDA end to end. Run only inside Windows Sandbox (scripts/e2e_sandbox.py)
or on a throwaway CI runner (.github/workflows/e2e.yml).

Never run these on a desktop where your own NVDA runs: starting a second NVDA
restarts the running one. They skip unless ASC_E2E_NVDA names a portable NVDA.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.integration.conftest import free_port, run_asc

from agent_speech_check.nvda.nvda_config import RelayAddress, render
from agent_speech_check.remote.relay import fingerprint
from agent_speech_check.windows import paths, winapi
from agent_speech_check.windows.wait import poll_until

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif("ASC_E2E_NVDA" not in os.environ, reason="needs ASC_E2E_NVDA"),
]

PAGE = """<!doctype html><html><head><title>asc test page</title></head><body>
<h1>First heading</h1><p>Some text.</p>
<h2>Second heading</h2>
<label>Your name <input type="text"></label>
</body></html>
"""


def insert_held() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x2D) & 0x8000)


def wait_connected(timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if "nvda: connected" in run_asc("status").stdout:
            return
        time.sleep(1)
    raise AssertionError("NVDA did not connect")


def nvda_log(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """This test's own NVDA log, where the e2e runs collect it."""
    return Path(os.environ.get("ASC_E2E_OUT", tmp_path)) / f"{request.node.name}.nvda.log"


@pytest.fixture
def real_nvda(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """A started `launch = false` desktop mode with a real NVDA joined to it."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    port = free_port()
    root = tmp_path / "agent-speech-check"
    root.mkdir()
    (root / "config.toml").write_text(
        f'mode = "desktop"\n[desktop]\nport = {port}\nlaunch = false\n', encoding="utf-8"
    )
    started = run_asc("start")
    assert started.returncode == 0, started.stderr
    folder = root / "desktop"
    channel = json.loads((folder / "relay.json").read_text(encoding="utf-8"))["channel"]
    config = tmp_path / "nvda_config"
    config.mkdir()
    ini = render(RelayAddress("127.0.0.1", port, channel, fingerprint(folder / "relay_cert.pem")))
    (config / "nvda.ini").write_text(ini, encoding="utf-8")
    nvda = Path(os.environ["ASC_E2E_NVDA"]) / "nvda.exe"
    log = nvda_log(request, tmp_path)
    process = subprocess.Popen(  # noqa: S603 - the throwaway machine's portable NVDA
        [str(nvda), f"--config-path={config}", f"--log-file={log}", "-l", "20"]
    )
    try:
        wait_connected()
        yield
    finally:
        run_asc("stop")
        process.kill()
        process.wait(30)


@pytest.mark.usefixtures("real_nvda")
def test_real_nvda_joins_speaks_and_releases() -> None:
    joined = run_asc(
        "wait", "Connected as controlled computer", "--since", "0", "--timeout", "20000"
    )
    assert joined.returncode == 0, joined.stderr

    pressed = run_asc("press", "NVDA+T", "--settle", "800")
    assert pressed.returncode == 0, pressed.stderr
    assert pressed.stdout.startswith('said: "'), pressed.stdout
    assert not insert_held()


@pytest.mark.usefixtures("real_nvda")
def test_mode_sounds_arrive_with_volume_zero(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    browser = subprocess.Popen(  # noqa: S603 - Windows' own mshta
        [r"C:\Windows\System32\mshta.exe", str(page)]
    )
    try:
        loaded = run_asc("wait", "asc test page", "--timeout", "30000")
        assert loaded.returncode == 0, loaded.stderr
        heading = run_asc("press", "H", "--expect", "Second heading", "--settle", "800")
        assert heading.returncode == 0, heading.stdout + heading.stderr
        into_field = run_asc("press", "Tab", "--settle", "1000")
        assert into_field.returncode == 0, into_field.stderr
        assert "mode: focus" in into_field.stdout, into_field.stdout
        status = run_asc("status").stdout
        assert "browse/focus: focus" in status
    finally:
        browser.kill()


APP = """<!doctype html><html><head><title>asc app</title></head><body>
<h1>asc app</h1>
<button>OK</button>
<label>Name <input type="text"></label>
</body></html>
"""


def test_desktop_mode_end_to_end(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """asc gets, launches and stops its own NVDA, and drives an app."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    fetched = run_asc("get-nvda", "--installer", os.environ["ASC_E2E_INSTALLER"], timeout=300)
    assert fetched.returncode == 0, fetched.stderr
    started = run_asc(
        "start",
        "--mode",
        "desktop",
        "--nvda-setting",
        "presentation.reportTooltips=False",
    )
    assert started.returncode == 0, started.stderr
    # Its NVDA runs on this desktop, so asc's keys land here: it has a panic key.
    assert "panic key: Control+Shift+Alt+F12 stops asc and releases keys" in started.stdout
    try:
        wait_connected()
        assert "Report title" in run_asc("keys", "report title", "--mode", "desktop").stdout

        page = tmp_path / "app.html"
        page.write_text(APP, encoding="utf-8")
        launched = run_asc("launch", f'mshta.exe "{page}"', "--title", "asc app")
        assert launched.returncode == 0, launched.stderr

        ok = run_asc("press", "Tab", "--expect", "OK button")
        assert ok.returncode == 0, ok.stdout + ok.stderr
        wrong = run_asc("press", "NVDA+T", "--expect", "Cancel button", "--timeout", "2000")
        assert wrong.returncode == 1
        assert "said: " in wrong.stderr

        field = run_asc("press", "Tab", "--settle", "800")
        assert "mode: focus" in field.stdout, field.stdout
        assert run_asc("type", "hi").returncode == 0
        line = run_asc("press", "NVDA+Up", "--expect", "hi")
        assert line.returncode == 0, line.stdout + line.stderr

        # Batch launchers, starting the app or running it: NVDA hears the
        # app, not the batch file's console.
        for name, runs in (("start", 'start "" mshta.exe'), ("run", "mshta.exe")):
            app_title = f"asc {name}"
            (tmp_path / f"{name}.html").write_text(APP.replace("asc app", app_title), "utf-8")
            launcher = tmp_path / f"{name}.cmd"
            launcher.write_text(f'{runs} "%~dp0{name}.html"\n', "utf-8")
            out = run_asc("launch", str(launcher), "--title", app_title, "--timeout", "10000")
            assert out.returncode == 0, out.stdout + out.stderr
            assert "terminal" not in out.stdout.lower(), out.stdout
            said = run_asc("press", "NVDA+t")
            assert app_title in said.stdout, said.stdout
    finally:
        subprocess.run(["taskkill", "/f", "/im", "mshta.exe"], check=False)  # noqa: S607
        try:
            stopped = run_asc("stop")
        finally:
            # asc's own NVDA logs into its mode folder, which goes with tmp_path.
            # Kept even when stop hangs, the case the log is most wanted for.
            log = paths.mode_dir("desktop") / "nvda.log"
            if log.exists():
                shutil.copy(log, nvda_log(request, tmp_path))
    assert stopped.returncode == 0
    assert poll_until(lambda: not winapi.process_ids("nvda.exe"), 15, 0.5)
    assert not insert_held()
