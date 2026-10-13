"""Real NVDA end to end. Run only inside Windows Sandbox: scripts/e2e_sandbox.py.

Never run these on a desktop where your own NVDA runs: starting a second NVDA
restarts the running one. They skip unless ASC_E2E_NVDA names a portable NVDA.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.integration.conftest import free_port, run_asc

from agent_speech_check import winapi
from agent_speech_check.nvda_config import RelayAddress, render
from agent_speech_check.relay import fingerprint
from agent_speech_check.setup import SYMBOL_LEVELS

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


def wait_connected(target: str, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if "nvda: connected" in run_asc("status", "--target", target).stdout:
            return
        time.sleep(1)
    raise AssertionError("NVDA did not connect")


@pytest.fixture
def real_nvda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A started `launch = false` desktop target with a real NVDA joined to it."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    port = free_port()
    root = tmp_path / "agent-speech-check"
    root.mkdir()
    (root / "config.toml").write_text(
        f'[targets.e]\nmode = "desktop"\nport = {port}\nlaunch = false\n', encoding="utf-8"
    )
    started = run_asc("start", "--target", "e")
    assert started.returncode == 0, started.stderr
    folder = root / "e"
    channel = json.loads((folder / "relay.json").read_text(encoding="utf-8"))["channel"]
    config = tmp_path / "nvda_config"
    config.mkdir()
    ini = render(RelayAddress("127.0.0.1", port, channel, fingerprint(folder / "relay_cert.pem")))
    (config / "nvda.ini").write_text(ini, encoding="utf-8")
    nvda = Path(os.environ["ASC_E2E_NVDA"]) / "nvda.exe"
    log = Path(os.environ.get("ASC_E2E_OUT", tmp_path)) / "nvda.log"
    process = subprocess.Popen(  # noqa: S603 - the sandbox's portable NVDA
        [str(nvda), f"--config-path={config}", f"--log-file={log}", "-l", "20"]
    )
    try:
        wait_connected("e")
        yield "e"
    finally:
        run_asc("stop", "--target", "e")
        process.kill()
        process.wait(30)


def test_real_nvda_joins_speaks_and_releases(real_nvda: str) -> None:
    joined = run_asc(
        "wait",
        "Connected as controlled computer",
        "--since",
        "0",
        "--timeout",
        "20000",
        "--target",
        real_nvda,
    )
    assert joined.returncode == 0, joined.stderr

    pressed = run_asc("press", "NVDA+T", "--settle", "800", "--target", real_nvda)
    assert pressed.returncode == 0, pressed.stderr
    assert pressed.stdout.startswith('said: "'), pressed.stdout
    assert not insert_held()


def test_mode_sounds_arrive_with_volume_zero(real_nvda: str, tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    browser = subprocess.Popen(  # noqa: S603 - Windows' own mshta, inside the sandbox
        [r"C:\Windows\System32\mshta.exe", str(page)]
    )
    try:
        loaded = run_asc("wait", "asc test page", "--timeout", "30000", "--target", real_nvda)
        assert loaded.returncode == 0, loaded.stderr
        heading = run_asc(
            "press", "H", "--expect", "Second heading", "--settle", "800", "--target", real_nvda
        )
        assert heading.returncode == 0, heading.stdout + heading.stderr
        into_field = run_asc("press", "Tab", "--settle", "1000", "--target", real_nvda)
        assert into_field.returncode == 0, into_field.stderr
        assert "mode: focus" in into_field.stdout, into_field.stdout
        status = run_asc("status", "--target", real_nvda).stdout
        assert "browse/focus: focus" in status
    finally:
        browser.kill()


APP = """<!doctype html><html><head><title>asc app</title></head><body>
<h1>asc app</h1>
<button>OK</button>
<label>Name <input type="text"></label>
</body></html>
"""


def test_desktop_mode_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The M2 check: asc gets, launches and stops its own NVDA, and drives an app."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    fetched = run_asc("get-nvda", "--installer", os.environ["ASC_E2E_INSTALLER"], timeout=300)
    assert fetched.returncode == 0, fetched.stderr
    started = run_asc(
        "start",
        "--mode",
        "desktop",
        "--target",
        "x",
        "--nvda-setting",
        "presentation.reportTooltips=False",
    )
    assert started.returncode == 0, started.stderr
    try:
        wait_connected("x")
        assert "Report title" in run_asc("keys", "report title", "--target", "x").stdout

        page = tmp_path / "app.html"
        page.write_text(APP, encoding="utf-8")
        launched = run_asc("launch", f'mshta.exe "{page}"', "--title", "asc app", "--target", "x")
        assert launched.returncode == 0, launched.stderr

        ok = run_asc("press", "Tab", "--expect", "OK button", "--target", "x")
        assert ok.returncode == 0, ok.stdout + ok.stderr
        wrong = run_asc(
            "press", "NVDA+T", "--expect", "Cancel button", "--timeout", "2000", "--target", "x"
        )
        assert wrong.returncode == 1
        assert "said: " in wrong.stderr

        field = run_asc("press", "Tab", "--settle", "800", "--target", "x")
        assert "mode: focus" in field.stdout, field.stdout
        assert run_asc("type", "hi", "--target", "x").returncode == 0
        line = run_asc("press", "NVDA+Up", "--expect", "hi", "--target", "x")
        assert line.returncode == 0, line.stdout + line.stderr
    finally:
        subprocess.run(["taskkill", "/f", "/im", "mshta.exe"], check=False)  # noqa: S607
        stopped = run_asc("stop", "--target", "x")
    assert stopped.returncode == 0
    deadline = time.monotonic() + 15
    while winapi.process_ids("nvda.exe") and time.monotonic() < deadline:
        time.sleep(0.5)
    assert winapi.process_ids("nvda.exe") == []
    assert not insert_held()


FIELD = """<!doctype html><html><head><title>asc field</title></head><body>
<label>Name <input type="text" value="Wow!"></label>
</body></html>
"""


# keep: NVDA's own default (some), which doesn't say "!" either, so "all" is seen to change it.
@pytest.mark.parametrize(("level", "bang"), [("keep", False), ("all", True), ("none", False)])
def test_stored_punctuation_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str, bang: bool
) -> None:
    """The values `asc setup` stores for a punctuation level are the ones NVDA reads."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    fetched = run_asc("get-nvda", "--installer", os.environ["ASC_E2E_INSTALLER"], timeout=300)
    assert fetched.returncode == 0, fetched.stderr
    assert run_asc("config", "set", "mode", "desktop", "--target", "p").returncode == 0
    if level != "keep":
        setting = f"speech.symbolLevel={SYMBOL_LEVELS[level]}"
        assert run_asc("config", "set", "nvda_settings", setting, "--target", "p").returncode == 0
    started = run_asc("start", "--target", "p")
    assert started.returncode == 0, started.stderr
    try:
        wait_connected("p")
        page = tmp_path / "field.html"
        page.write_text(FIELD, encoding="utf-8")
        launched = run_asc("launch", f'mshta.exe "{page}"', "--title", "asc field", "--target", "p")
        assert launched.returncode == 0, launched.stderr
        field = run_asc("press", "Tab", "--expect", "Wow", "--settle", "800", "--target", "p")
        assert field.returncode == 0, field.stdout + field.stderr
        assert ("bang" in field.stdout.lower()) == bang, field.stdout
    finally:
        subprocess.run(["taskkill", "/f", "/im", "mshta.exe"], check=False)  # noqa: S607
        run_asc("stop", "--target", "p")
