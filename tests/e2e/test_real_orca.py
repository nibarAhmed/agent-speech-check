"""Real Orca end to end, on Linux. They skip unless ASC_E2E_ORCA is set, as the
e2e workflow does in its throwaway container. Elsewhere asc starts no second
Orca for a user, so with your own Orca running these fail instead.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import psutil
import pytest
from tests.integration.conftest import run_asc, wait_until

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif("ASC_E2E_ORCA" not in os.environ, reason="needs ASC_E2E_ORCA"),
]

# The system Python runs it: it has GTK's bindings, the venv's has not.
FORM = """import subprocess
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
win = Gtk.Window(title="asc e2e form")
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
entry = Gtk.Entry()
entry.get_accessible().set_name("Name")
box.add(entry)
box.add(Gtk.Button(label="Save"))
win.add(box)
win.connect("destroy", Gtk.main_quit)
# Like a daemon an app starts: it leaves the app's process group.
subprocess.Popen(["sleep", "3601"], start_new_session=True)
win.show_all()
win.present()
Gtk.main()
"""

STARTED = {"Xvfb", "dbus-daemon", "openbox", "orca", "xdotool"}


def left_running() -> list[str]:
    # Zombies don't count: a container's first process may never reap them.
    return [
        p.info["name"]
        for p in psutil.process_iter(["name", "status", "cmdline"])
        if (p.info["name"] in STARTED or p.info["cmdline"] == ["sleep", "3601"])
        and p.info["status"] != psutil.STATUS_ZOMBIE
    ]


def left_folders() -> list[Path]:
    return list(Path(tempfile.gettempdir()).glob("asc-orca-*"))


@pytest.mark.parametrize("over_ssh", [False, True], ids=["here", "over ssh"])
def test_orca_reads_a_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, over_ssh: bool
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    if over_ssh:
        # A destination whose files are this machine's, as e2e.yml's root@localhost.
        destination = os.environ.get("ASC_E2E_SSH")
        if not destination:
            pytest.skip("needs ASC_E2E_SSH")
        monkeypatch.setenv("ASC_INSTANCE", "e2e-orca")
        home = tmp_path / "state" / "agent-speech-check" / "instances" / "e2e-orca"
        home.mkdir(parents=True)
        remote = f'mode = "remote"\n[remote]\nos = "linux"\nssh = "{destination}"\n'
        (home / "config.toml").write_text(remote, encoding="utf-8")
    form = tmp_path / "form.py"
    form.write_text(FORM, encoding="utf-8")
    assert not left_running()
    started = run_asc("start")  # desktop, the default on Linux, or the instance's remote mode
    assert started.returncode == 0, started.stderr
    try:
        launched = run_asc("launch", f"/usr/bin/python3 {form}", "--title", "asc e2e form")
        assert launched.returncode == 0, launched.stderr
        assert "Name" in launched.stdout
        # Orca+T, the time: an Orca modifier command, which needs Insert held.
        clock = run_asc("press", "Orca+T", "--expect", r"\d+:\d\d")
        assert clock.returncode == 0, clock.stdout + clock.stderr
        typed = run_asc("type", "Ab!")
        assert typed.returncode == 0, typed.stderr
        for char in "Ab!":
            assert f'said: "{char}"' in typed.stdout, typed.stdout
        tab = run_asc("press", "Tab", "--expect", "(?i)save button")
        assert tab.returncode == 0, tab.stdout + tab.stderr
    finally:
        stopped = run_asc("stop")
    assert stopped.returncode == 0, stopped.stderr
    assert wait_until(lambda: not left_running() and not left_folders()), (
        left_running(),
        left_folders(),
    )
