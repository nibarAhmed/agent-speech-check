"""Real Orca end to end, on Linux. They skip unless ASC_E2E_ORCA is set, as the
e2e workflow does in its throwaway container. Elsewhere asc starts no second
Orca for a user, so with your own Orca running these fail instead.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
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
        for p in psutil.process_iter(["name", "status", "cmdline", "exe"])
        if (
            p.info["name"] in STARTED
            or p.info["cmdline"] == ["sleep", "3601"]
            or (p.info["exe"] or "").startswith("/usr/lib/firefox/")
        )
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
        ini = run_asc("status", "--nvda-ini")
        assert (ini.returncode, "Orca" in ini.stderr) == (3, True), ini.stderr
    finally:
        stopped = run_asc("stop")
    assert stopped.returncode == 0, stopped.stderr
    assert wait_until(lambda: not left_running() and not left_folders()), (
        left_running(),
        left_folders(),
    )


PAGE = """<!doctype html><html lang="en"><title>asc e2e page</title>
<h1>Welcome heading</h1><p>Some text.</p><input aria-label="Search box"><h2>Second heading</h2>
"""

# Without them Firefox opens on its welcome and terms of use pages.
PREFS = """user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.preonboarding.enabled", false);
user_pref("termsofuse.bypassNotification", true);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
"""


def test_orca_browses_a_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "user.js").write_text(PREFS, encoding="utf-8")
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    assert not left_running()
    started = run_asc("start")
    assert started.returncode == 0, started.stderr
    try:
        firefox = f"firefox --profile {profile} --no-remote {page.as_uri()}"
        launched = run_asc("launch", firefox, "--title", "asc e2e page", "--timeout", "60000")
        assert launched.returncode == 0, launched.stdout + launched.stderr
        assert "mode: browse" in launched.stdout
        heading = run_asc("press", "H", "--expect", "Second heading")
        assert heading.returncode == 0, heading.stdout + heading.stderr
        focus = run_asc("press", "Orca+A", "--expect", "Focus mode")
        assert focus.returncode == 0, focus.stdout + focus.stderr
        assert "browse/focus: focus" in run_asc("status").stdout.splitlines()
    finally:
        stopped = run_asc("stop")
    assert stopped.returncode == 0, stopped.stderr
    assert wait_until(lambda: not left_running() and not left_folders()), (
        left_running(),
        left_folders(),
    )


WATCH_FORM = """import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
win = Gtk.Window(title="asc e2e watch form")
entry = Gtk.Entry()
entry.get_accessible().set_name("Name")
win.add(entry)
win.connect("destroy", Gtk.main_quit)
win.show_all()
win.present()
Gtk.main()
"""


def gdbus(method: str, *args: str) -> bool:
    command = ["gdbus", "call", "--session", "--dest", "org.gnome.Orca1.Service"]
    path = ["--object-path", "/org/gnome/Orca1/Service", "--method", method, *args]
    run = [*command, *path]
    done = subprocess.run(run, capture_output=True, check=False, timeout=30)  # noqa: S603 - fixed
    return done.returncode == 0


def ended(process: psutil.Process) -> bool:
    # A container's first process may never reap what it inherits.
    try:
        return process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def answers() -> bool:
    return gdbus("org.gnome.Orca1.Service.GetVersion")


@pytest.fixture
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[psutil.Process]]:
    """A user's own desktop session and Orca, as watch mode finds them. Yields what it
    started, Orca last, for the test to add to: all of it is ended after."""
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    for name, value in {
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "GSETTINGS_BACKEND": "memory",
        "GTK_A11Y": "atspi",
        "NO_AT_BRIDGE": "0",
        "GDK_BACKEND": "x11",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    started: list[psutil.Process] = []
    try:
        read, write = os.pipe()
        xvfb = ["Xvfb", "-displayfd", str(write), "-noreset", "-screen", "0", "1024x768x24"]
        started.append(psutil.Popen(xvfb, pass_fds=(write,), stderr=subprocess.DEVNULL))
        os.close(write)
        with os.fdopen(read) as display:
            monkeypatch.setenv("DISPLAY", ":" + display.readline().strip())
        bus = ["dbus-daemon", "--session", "--nofork", "--print-address"]
        dbus = psutil.Popen([*bus, f"--address=unix:dir={runtime}"], stdout=subprocess.PIPE)
        started.append(dbus)
        assert dbus.stdout is not None
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", dbus.stdout.readline().decode().strip())
        started.append(psutil.Popen(["openbox"], stderr=subprocess.DEVNULL))
        started.append(psutil.Popen(["orca"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        assert wait_until(answers, 30)
        yield started
    finally:
        for process in reversed(started):
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()
        assert wait_until(lambda: all(map(ended, started)))


def test_watch_hears_your_own_orca(tmp_path: Path, desktop: list[psutil.Process]) -> None:
    app = tmp_path / "form.py"
    app.write_text(WATCH_FORM, encoding="utf-8")
    refused = run_asc("start", "--mode", "watch")
    assert refused.returncode == 3, refused.stdout + refused.stderr
    (line,) = re.findall(r"restart Orca for asc: (.+)", refused.stderr)
    assert "orca --replace" in line
    # As the user runs it, at their desktop.
    shell = ["bash", "-c", f"{line} echo $!"]
    restart = subprocess.run(shell, capture_output=True, text=True, check=True)  # noqa: S603 - asc's line
    old, own = desktop[-1], psutil.Process(int(restart.stdout))
    desktop.append(own)
    assert isinstance(old, psutil.Popen)
    assert wait_until(lambda: old.poll() is not None and answers(), 30)
    started = run_asc("start", "--mode", "watch")
    assert started.returncode == 0, started.stdout + started.stderr
    try:
        mark = run_asc("mark").stdout.split()[-1]
        assert gdbus("org.gnome.Orca1.Service.SpeakMessage", "asc e2e watch")
        heard = run_asc("wait", "--since", mark, "asc e2e watch")
        assert heard.returncode == 0, heard.stdout + heard.stderr
        launched = run_asc("launch", f"/usr/bin/python3 {app}", "--title", "asc e2e watch form")
        assert launched.returncode == 0, launched.stdout + launched.stderr
        assert "Name" in launched.stdout
    finally:
        stopped = run_asc("stop")
        apps = [
            p for p in psutil.process_iter(["cmdline"]) if str(app) in (p.info["cmdline"] or [])
        ]
        desktop.extend(apps)
    assert stopped.returncode == 0, stopped.stderr
    assert wait_until(lambda: not left_folders()), left_folders()
    # The user's Orca, and the app on their desktop, outlive asc.
    assert own.status() != psutil.STATUS_ZOMBIE
    assert apps


@pytest.mark.parametrize("over_ssh", [False, True], ids=["here", "over ssh"])
def test_keys_reads_orcas_own_help(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, over_ssh: bool
) -> None:
    if over_ssh:
        destination = os.environ.get("ASC_E2E_SSH")
        if not destination:
            pytest.skip("needs ASC_E2E_SSH")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("ASC_INSTANCE", "e2e-orca-keys")
        home = tmp_path / "state" / "agent-speech-check" / "instances" / "e2e-orca-keys"
        home.mkdir(parents=True)
        remote = f'mode = "remote"\n[remote]\nos = "linux"\nssh = "{destination}"\n'
        (home / "config.toml").write_text(remote, encoding="utf-8")
        # Only the other machine's help, which ssh reads with its own environment.
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))
    found = run_asc("keys", "browse", "mode")
    assert "Orca+A  Switch between browse mode and focus mode" in found.stdout.splitlines(), (
        found.stderr
    )
    laptop = run_asc("keys", "--layout", "laptop", "title", "bar")
    assert laptop.stdout.splitlines() == ["Orca+Slash  Present the title bar"], laptop.stderr
