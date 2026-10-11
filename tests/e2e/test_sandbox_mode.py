"""Sandbox mode end to end, run on the host (a sandbox can't run inside one).

Needs `asc sandbox-setup` done once, and the NVDA installer: set
ASC_E2E_SANDBOX=1 and ASC_E2E_INSTALLER to the installer (scripts/e2e_sandbox.py
downloads it to .e2e/in). Your own NVDA keeps running; the test checks that
its process is untouched.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest
from tests.integration.conftest import run_asc

from agent_speech_check import nvda, sandbox, winapi

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


def test_sandbox_mode_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "agent-speech-check"
    installer = nvda.installer_path(nvda.VERSION)
    installer.parent.mkdir(parents=True)
    shutil.copy(os.environ["ASC_E2E_INSTALLER"], installer)
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.html").write_text(APP, encoding="utf-8")
    (root / "targets.toml").write_text(
        f"[targets.sb]\nmode = 'sandbox'\nshare = ['{app.as_posix()}']\n", encoding="utf-8"
    )
    users_nvda = sorted(winapi.process_ids("nvda.exe"))

    started = run_asc("start", "--target", "sb", timeout=420)
    assert started.returncode == 0, started.stdout + started.stderr
    assert "nvda: connected" in started.stdout
    assert "sandbox: running" in started.stdout
    try:
        page = sandbox.share_folder(str(app)) + "\\app.html"
        launched = run_asc("launch", f'mshta.exe "{page}"', "--title", "asc app", "--target", "sb")
        assert launched.returncode == 0, launched.stderr
        ok = run_asc("press", "Tab", "--expect", "OK button", "--target", "sb")
        assert ok.returncode == 0, ok.stdout + ok.stderr
        field = run_asc("press", "Tab", "--settle", "800", "--target", "sb")
        assert "mode: focus" in field.stdout, field.stdout
        assert run_asc("type", "hi", "--target", "sb").returncode == 0
        line = run_asc("press", "NVDA+Up", "--expect", "hi", "--target", "sb")
        assert line.returncode == 0, line.stdout + line.stderr
    finally:
        stopped = run_asc("stop", "--target", "sb", timeout=120)
    assert stopped.returncode == 0
    assert sandbox.wait_gone(120)
    assert sorted(winapi.process_ids("nvda.exe")) == users_nvda
    time.sleep(1)
