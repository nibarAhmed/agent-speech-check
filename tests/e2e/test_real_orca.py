"""Real Orca end to end, on Linux. They skip unless ASC_E2E_ORCA is set, as the
e2e workflow does in its throwaway container. Elsewhere asc starts no second
Orca for a user, so with your own Orca running these fail instead.
"""

from __future__ import annotations

import contextlib
import os
import select
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest
from tests.integration.conftest import asc_command, run_asc, wait_until

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
        # Reparent Orca to PID 1 (docker --init), as a desktop session does, so
        # killing it via `orca --replace` is reaped immediately instead of hanging
        # as a zombie while pytest waits on `asc start`.
        launched = subprocess.run(
            ["/bin/bash", "-c", "setsid orca >/dev/null 2>&1 & echo $!"],
            capture_output=True,
            text=True,
            check=True,
        )
        started.append(psutil.Process(int(launched.stdout)))
        assert wait_until(answers, 30)
        yield started
    finally:
        for pid in _orca_pids():
            with contextlib.suppress(psutil.NoSuchProcess):
                started.append(psutil.Process(pid))
        for process in reversed(started):
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()
        assert wait_until(lambda: all(map(ended, started)))


def run_asc_tty(reply: str, *args: str, timeout: float = 60) -> tuple[int, str]:
    """Run `asc` connected to a real PTY and send `reply` once prompted."""
    if sys.platform == "win32":
        raise RuntimeError("Linux only")
    import pty  # noqa: PLC0415 - Linux only

    master, slave = pty.openpty()
    proc = subprocess.Popen(  # noqa: S603 - asc itself
        asc_command(*args),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    sent = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                proc.kill()
                raise TimeoutError(f"asc {args} timed out: {output.decode(errors='replace')}")
            ready, _, _ = select.select([master], [], [], min(0.2, left))
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if not sent and b"Restart Orca now?" in output:
                    os.write(master, reply.encode())
                    sent = True
            elif proc.poll() is not None:
                break
        return proc.wait(timeout=max(1.0, deadline - time.monotonic())), output.decode(
            errors="replace"
        )
    finally:
        os.close(master)


def _orca_pids() -> set[int]:
    return {
        p.pid
        for p in psutil.process_iter(["name", "status"])
        if p.info["name"] == "orca" and p.info["status"] != psutil.STATUS_ZOMBIE
    }


def test_watch_hears_your_own_orca(tmp_path: Path, desktop: list[psutil.Process]) -> None:
    app = tmp_path / "form.py"
    app.write_text(WATCH_FORM, encoding="utf-8")
    old = desktop[-1]

    # Non-interactive default (ask): refuses to restart and leaves Orca running.
    refused = run_asc("start", "--mode", "watch")
    assert refused.returncode == 3, refused.stdout + refused.stderr
    assert "asc start --mode watch in a terminal" in refused.stderr
    assert not ended(old)

    # Interactive terminal: answering no prints the manual command and leaves Orca alone.
    code, out = run_asc_tty("n\n", "start", "--mode", "watch")
    assert code == 3, out
    assert "orca --replace" in out
    assert not ended(old)

    # Interactive terminal: accepting (Enter) restarts Orca and attaches.
    code, out = run_asc_tty("\n", "start", "--mode", "watch")
    assert code == 0, out
    assert "restarted Orca with asc's recording secret" in out
    assert wait_until(lambda: ended(old) and answers(), 30)
    (own_pid,) = _orca_pids()
    own = psutil.Process(own_pid)
    desktop.append(own)
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
    assert not ended(own)
    assert apps

    # Subsequent start in the same session attaches without restarting Orca again.
    again = run_asc("start", "--mode", "watch")
    assert again.returncode == 0, again.stdout + again.stderr
    try:
        assert "restarted Orca" not in again.stdout
        assert _orca_pids() == {own_pid}
    finally:
        assert run_asc("stop").returncode == 0


def test_watch_restart_orca_policies(
    tmp_path: Path, desktop: list[psutil.Process], monkeypatch: pytest.MonkeyPatch
) -> None:
    old = desktop[-1]
    config = tmp_path / "state" / "agent-speech-check" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)

    # Policy never: prints the manual restart command and never restarts.
    config.write_text('[watch]\nrestart_orca = "never"\n', encoding="utf-8")
    never = run_asc("start", "--mode", "watch")
    assert never.returncode == 3, never.stdout + never.stderr
    assert "orca --replace" in never.stderr
    assert not ended(old)

    # Policy always without a desktop display: refuses so a headless shell cannot strand Orca.
    config.write_text('[watch]\nrestart_orca = "always"\n', encoding="utf-8")
    display = os.environ["DISPLAY"]
    monkeypatch.delenv("DISPLAY")
    headless = run_asc("start", "--mode", "watch")
    assert headless.returncode == 3, headless.stdout + headless.stderr
    assert "orca --replace" in headless.stderr
    assert not ended(old)
    monkeypatch.setenv("DISPLAY", display)

    # Policy always in a desktop session: restarts non-interactively and records speech.
    started = run_asc("start", "--mode", "watch")
    assert started.returncode == 0, started.stdout + started.stderr
    assert "restarted Orca with asc's recording secret" in started.stdout
    assert wait_until(lambda: ended(old) and answers(), 30)
    (own_pid,) = _orca_pids()
    desktop.append(psutil.Process(own_pid))
    try:
        mark = run_asc("mark").stdout.split()[-1]
        assert gdbus("org.gnome.Orca1.Service.SpeakMessage", "asc e2e always")
        heard = run_asc("wait", "--since", mark, "asc e2e always")
        assert heard.returncode == 0, heard.stdout + heard.stderr
    finally:
        assert run_asc("stop").returncode == 0


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
