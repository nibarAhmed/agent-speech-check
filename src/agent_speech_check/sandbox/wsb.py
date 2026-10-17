"""Windows Sandbox through the `wsb` CLI, its window kept out of the way.

Observed on Windows 11 Pro 26200:
- `wsb start` runs a sandbox with no window, but with no user session either:
  the LogonCommand doesn't run and `wsb exec --run-as ExistingLogin` fails.
- `wsb connect` logs the user in and opens a window.
- If the connect client is then closed, the session lives on, disconnected,
  and commands still run in it. But NVDA goes silent there: it connects, yet
  injected keys and focus changes produce no speech. So the client stays.
- Minimised or hidden, the connected sandbox works normally. A visible
  sandbox window takes focus and catches keys typed on the host, so it is
  hidden (behind a tray icon) or minimised the moment it appears, and kept so
  (`SandboxWindow`).
- A sandbox runs on when the process that started it dies. So a guard process
  starts with each one and stops it once its owner is gone (`guard`).

Pure parts (config XML, parsing `wsb --raw` output) are separate from the thin
shell that runs `wsb`, so they can be unit-tested.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import threading
import winreg
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from agent_speech_check.daemon import spawn
from agent_speech_check.errors import (
    NotConnected,
    SandboxBroken,
    SandboxFailed,
    SandboxNeedsPro,
    WsbMissing,
)
from agent_speech_check.validate import Fields
from agent_speech_check.windows import powershell, winapi
from agent_speech_check.windows.wait import poll_until

log = logging.getLogger(__name__)

WINDOW_TITLE = "Windows Sandbox"
VM_IMAGE = "vmmemWindowsSandbox"
SESSION_TIMEOUT_S = 120
WSB_TIMEOUT_S = 120
POLL_S = 0.1
# The client shows its window again now and then, and shows it on stop too.
KEEP_WINDOW_EVERY_S = 0.05
GONE_POLL_S = 0.25
_HOME_EDITIONS = ("Core", "CoreN", "CoreSingleLanguage", "CoreCountrySpecific")


@dataclass(frozen=True)
class Mapped:
    host: str
    sandbox: str
    read_only: bool = True


def config_xml(mapped: list[Mapped], *, networking: bool, logon: str | None = None) -> str:
    """A Windows Sandbox configuration, with every value XML-escaped."""
    folders = "".join(
        "<MappedFolder>"
        f"<HostFolder>{escape(m.host)}</HostFolder>"
        f"<SandboxFolder>{escape(m.sandbox)}</SandboxFolder>"
        f"<ReadOnly>{'true' if m.read_only else 'false'}</ReadOnly>"
        "</MappedFolder>"
        for m in mapped
    )
    logon_xml = f"<LogonCommand><Command>{escape(logon)}</Command></LogonCommand>" if logon else ""
    return (
        "<Configuration>"
        f"<Networking>{'Enable' if networking else 'Disable'}</Networking>"
        "<AudioInput>Disable</AudioInput><VideoInput>Disable</VideoInput>"
        "<ClipboardRedirection>Disable</ClipboardRedirection>"
        f"<MappedFolders>{folders}</MappedFolders>{logon_xml}"
        "</Configuration>"
    )


# ---------------------------------------------------------------- parsing `wsb --raw`


def _json(text: str, what: str) -> Fields:
    try:
        raw = json.loads(text)
    except ValueError:
        raise SandboxBroken(what, text.strip()[:200]) from None
    return Fields(raw, f"wsb {what} output", NotConnected)


def parse_start(text: str) -> str:
    return _json(text, "start").text("Id", max_len=100)


def parse_ip(text: str) -> str | None:
    networks = _json(text, "ip").items("Networks")
    for network in networks:
        address = Fields(network, "wsb ip network", NotConnected).opt_text(
            "IpV4Address", max_len=50
        )
        if address:
            return address
    return None


def parse_exec(text: str) -> int:
    return _json(text, "exec").number("ExitCode", low=-(2**31), high=2**32)


def parse_list(text: str) -> list[str]:
    environments = _json(text, "list").items("WindowsSandboxEnvironments")
    return [Fields(e, "wsb list entry", NotConnected).text("Id", max_len=100) for e in environments]


def window_step(window: str, *, shown: bool, minimized: bool) -> tuple[str | None, bool]:
    """For one visible sandbox window: what to do ("hide", "minimize" or None),
    and whether the user now wants it shown. `window` is sandbox mode's setting."""
    if window == "tray":
        if shown and not minimized:
            return None, True
        # Minimising a window the user showed sends it back to the tray.
        return "hide", False
    if window == "taskbar" and not minimized:
        return "minimize", shown
    return None, shown


def is_home_edition(edition_id: str) -> bool:
    return edition_id in _HOME_EDITIONS


# Where things sit inside the sandbox.
IN = r"C:\asc\in"  # read-only: the script below
NVDA_IN = r"C:\asc\nvda"  # read-only: the NVDA installer
CONFIG = r"C:\asc\config"  # writable: nvda.ini, NVDA's log
PORTABLE = r"C:\nvda"
SHARE = r"C:\share"
SCRIPT = "start_nvda.cmd"


def nvda_script(installer: str | None) -> str:
    """Runs in the sandbox's user session: mute it, make NVDA portable, start it.

    With `installer`, NVDA_IN holds that installer, which makes the portable
    copy. Without, NVDA_IN is your own portable NVDA, copied because the mapped
    folder is read-only. Stopping Windows Audio silences the sandbox completely.
    """
    if installer is None:
        make = f"robocopy {NVDA_IN} {PORTABLE} /e /njh /njs /nfl /ndl /np > nul"
    else:
        make = f'"{NVDA_IN}\\{installer}" --create-portable-silent --portable-path={PORTABLE}'
    return "\r\n".join(
        [
            "@echo off",
            "net stop audiosrv /y > nul 2>&1",
            make,
            ":wait",
            f"if not exist {PORTABLE}\\nvda.exe (",
            "  timeout /t 1 /nobreak > nul",
            "  goto wait",
            ")",
            "timeout /t 3 /nobreak > nul",
            f'start "" {PORTABLE}\\nvda.exe --config-path={CONFIG} '
            f"--log-file={CONFIG}\\nvda.log -l 20",
            "",
        ]
    )


def share_folder(host_path: str) -> str:
    """Where a shared host folder appears in the sandbox."""
    name = host_path.rstrip("\\/").replace("/", "\\").rsplit("\\", 1)[-1] or "drive"
    return f"{SHARE}\\{name.rstrip(':')}"


def session_folders(
    in_dir: str, nvda_dir: str, config_dir: str, shares: tuple[str, ...]
) -> list[Mapped]:
    return [
        Mapped(in_dir, IN),
        Mapped(nvda_dir, NVDA_IN),
        Mapped(config_dir, CONFIG, read_only=False),
        *(Mapped(share, share_folder(share)) for share in shares),
    ]


def script_command() -> str:
    return f"cmd /c {IN}\\{SCRIPT}"


LAUNCH_SCRIPT = "launch.ps1"
LAUNCH_PATH = f"{IN}\\{LAUNCH_SCRIPT}"
NO_WINDOW_EXIT = 3

# An app started through `wsb exec` doesn't get the foreground (Windows'
# focus-stealing rules), so NVDA never hears of it (observed). This script
# starts the app, waits for its new window, and brings it forward by attaching
# to the foreground thread's input, the documented way to be allowed to.
# A process's MainWindowHandle isn't always the window a user sees: mshta's is
# a hidden "Internet Explorer_Hidden" window, which NVDA reads as "unknown"
# (observed). So it leaves the focus alone when the app already has it, and
# otherwise picks the app's topmost visible window with a title.
# A batch file runs with `call` in the script's hidden console: `start` would
# open a console for it, which NVDA reads and which takes the focus (observed).
# After recent input that forced switch is silent: Windows tells NVDA nothing
# (observed). So first, as `mouse.nudge` does on the host, it sends a mouse
# input that moves nothing and lets any process take the foreground.
LAUNCH_PS1 = r"""param([string]$Command, [switch]$Batch)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class AscFocus {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a, uint b, bool attach);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
    // One all-zero INPUT: a mouse input that moves nothing. 40 bytes on x64.
    [StructLayout(LayoutKind.Sequential, Size = 40)] struct StillMouse { public uint type; }
    [DllImport("user32.dll")] static extern uint SendInput(uint n, StillMouse[] i, int size);
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);
    public static void Unlock() {
        SendInput(1, new StillMouse[1], Marshal.SizeOf(typeof(StillMouse)));
        AllowSetForegroundWindow(-1);
    }
    delegate bool EnumProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr p);
    static uint Owner(IntPtr h) { uint pid; GetWindowThreadProcessId(h, out pid); return pid; }
    public static void FocusApp(int pid, IntPtr main) {
        if (Owner(GetForegroundWindow()) == (uint)pid) { return; }
        IntPtr pick = main;
        EnumWindows((h, p) => {
            if (Owner(h) == (uint)pid && IsWindowVisible(h) && GetWindowTextLength(h) > 0) {
                pick = h;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        Focus(pick);
    }
    static void Focus(IntPtr h) {
        uint fg = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);
        uint me = GetCurrentThreadId();
        AttachThreadInput(me, fg, true);
        ShowWindow(h, 9);
        BringWindowToTop(h);
        SetForegroundWindow(h);
        AttachThreadInput(me, fg, false);
    }
}
"@
$before = @(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } |
    ForEach-Object { $_.MainWindowHandle })
[AscFocus]::Unlock()
$run = if ($Batch) { "call $Command" } else { "start `"`" $Command" }
Start-Process cmd.exe -ArgumentList '/c', $run -WindowStyle Hidden
for ($i = 0; $i -lt 150; $i++) {
    Start-Sleep -Milliseconds 200
    $new = Get-Process |
        Where-Object { $_.MainWindowHandle -ne 0 -and $before -notcontains $_.MainWindowHandle } |
        Select-Object -First 1
    if ($new) { [AscFocus]::FocusApp($new.Id, $new.MainWindowHandle); exit 0 }
}
exit 3
"""


def is_batch(command: str) -> bool:
    """Whether the program `command` runs (its first word, maybe quoted) is a batch file."""
    if command.startswith('"'):
        program = command[1:].partition('"')[0]
    else:
        program = command.split(maxsplit=1)[0] if command.strip() else ""
    return program.lower().endswith((".cmd", ".bat"))


def launch_command(command: str) -> str:
    """Run the launch script on `command`."""
    quote = powershell.quote
    batch = " -Batch" if is_batch(command) else ""
    return powershell.command_line(f"& {quote(LAUNCH_PATH)} -Command {quote(command)}{batch}")


# ---------------------------------------------------------------- the thin shell


def edition_id() -> str:
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        value, _ = winreg.QueryValueEx(key, "EditionID")
    return str(value)


def require_available() -> None:
    if is_home_edition(edition_id()):
        raise SandboxNeedsPro
    if shutil.which("wsb") is None:
        raise WsbMissing


# Every error a wsb call raises.
FAILURES = (SandboxFailed, SandboxBroken)


def wsb(*args: str, what: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - the Windows Sandbox CLI
            ["wsb", *args, "--raw"],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=WSB_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise SandboxFailed(what, f"no answer within {WSB_TIMEOUT_S} s") from None
    if result.returncode:
        raise SandboxBroken(what, (result.stderr or result.stdout).strip()[:300])
    return result.stdout


def running() -> list[str]:
    return parse_list(wsb("list", what="list"))


def wait_gone(timeout_s: float) -> bool:
    """Wait for the last sandbox VM to exit. Starting a new one before that
    attaches to the session that is logging off."""
    return poll_until(lambda: not winapi.process_ids(VM_IMAGE), timeout_s, GONE_POLL_S)


def exec_as_user(sandbox_id: str, command: str) -> int:
    out = wsb("exec", "--id", sandbox_id, "--run-as", "ExistingLogin", "-c", command, what="exec")
    return parse_exec(out)


def _has_session(sandbox_id: str) -> bool:
    try:
        return exec_as_user(sandbox_id, "cmd /c exit 0") == 0
    except FAILURES:
        return False


class SandboxWindow:
    """Keeps the sandbox window where the `window` setting wants it: "tray" (hidden, the
    tray icon toggles it), "taskbar" (minimised) or "show". `start` it before
    the sandbox and `stop` it after (or use it as a context manager): `keep`
    runs on its own thread every KEEP_WINDOW_EVERY_S in between. `toggle` may
    run on the tray icon's thread."""

    def __init__(
        self,
        window: str,
        tooltip: str = "asc sandbox: show or hide",
        *,
        title: str = WINDOW_TITLE,
    ) -> None:
        self.window = window
        # The start of the windows to keep. Tests pass their own, so they never
        # touch a real sandbox that another asc has open.
        self.title = title
        self._tooltip = tooltip
        self._icon: winapi.TrayIcon | None = None
        self.shown = False
        self._hidden: list[int] = []
        self._client_pids: set[int] = set()
        self._return_to = 0
        # `toggle` (tray thread) and `keep` (keeping thread) both write `shown`.
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._keep_loop, name="sandbox-window", daemon=True)

    def start(self) -> None:
        """Add the tray icon (for "tray"; this waits up to 5 s), then start keeping."""
        if self.window == "tray":
            icon = winapi.TrayIcon(self._tooltip, self.toggle)
            if icon.start():
                self._icon = icon
            else:
                # A hidden window with no icon could never be shown again.
                log.warning("no tray icon, so the sandbox window is minimised instead")
                icon.stop()
                self.window = "taskbar"
        self._thread.start()

    def stop(self) -> None:
        """Stop keeping and remove the icon. Safe to call more than once, or before `start`."""
        self._done.set()
        if self._thread.is_alive():
            self._thread.join(5)
        if self._icon is not None:
            self._icon.stop()
            self._icon = None

    def __enter__(self) -> SandboxWindow:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _keep_loop(self) -> None:
        while not self._done.wait(KEEP_WINDOW_EVERY_S):
            self.keep()

    def remember_focus(self) -> None:
        """Note the window that has focus now, before the sandbox window opens."""
        self._return_to = winapi.foreground()

    def toggle(self) -> None:
        with self._lock:
            self.shown = not self.shown

    def keep(self) -> None:
        with self._lock:
            self._keep()

    def _keep(self) -> None:
        if self.shown:
            for hwnd in self._hidden:
                winapi.restore(hwnd)
            self._hidden.clear()
        acted = False
        for hwnd in winapi.windows_titled(self.title):
            minimized = winapi.is_minimized(hwnd)
            action, self.shown = window_step(self.window, shown=self.shown, minimized=minimized)
            if action:
                acted = True
                self._client_pids.add(winapi.window_pid(hwnd))
            if action == "hide":
                # Hidden straight from the foreground, the sandbox client kept
                # every key and click on the host. Minimising first makes it let go.
                if not minimized:
                    winapi.minimize(hwnd)
                winapi.hide(hwnd)
                self._hidden.append(hwnd)
            elif action == "minimize":
                winapi.minimize(hwnd)
        self._give_focus_back(acted=acted)

    def _give_focus_back(self, *, acted: bool) -> None:
        """The opening sandbox window takes focus, and the client keeps it even
        once its window is hidden or minimised. Give focus back to the window that had it,
        but only while an out-of-sight sandbox client window has it, or nothing
        has it just after a window was put away."""
        if not self._return_to or not (acted or self._client_pids):
            return  # nothing of the sandbox's has been put away yet
        current = winapi.foreground()
        if current == 0:
            stuck = acted
        else:
            stuck = winapi.window_pid(current) in self._client_pids and (
                not winapi.is_visible(current) or winapi.is_minimized(current)
            )
        if stuck:
            winapi.focus(self._return_to)


def _wait_session(sandbox_id: str) -> None:
    if not poll_until(lambda: _has_session(sandbox_id), SESSION_TIMEOUT_S, POLL_S):
        raise SandboxFailed("connect", f"no user session after {SESSION_TIMEOUT_S} s")


def start_connected(config: str, window: SandboxWindow, guard_err: Path) -> str:
    """Start a sandbox with a logged-in user, guarded so it goes with this
    process (`guard_err` takes the guard's errors). Call it while `window` keeps."""
    if not wait_gone(60):
        raise SandboxFailed("start", "the previous sandbox is still shutting down")
    sandbox_id = parse_start(wsb("start", "--config", config, what="start"))
    try:
        window.remember_focus()
        subprocess.Popen(["wsb", "connect", "--id", sandbox_id])  # noqa: S603, S607
        start_guard(os.getpid(), sandbox_id, guard_err)
        _wait_session(sandbox_id)
    except BaseException:
        # The caller never learns this id, and this process lives on, so its
        # guard won't stop the sandbox: stop it here.
        with contextlib.suppress(*FAILURES):
            stop(sandbox_id)
        raise
    return sandbox_id


def ip_address(sandbox_id: str) -> str | None:
    return parse_ip(wsb("ip", "--id", sandbox_id, what="ip"))


def stop(sandbox_id: str) -> None:
    wsb("stop", "--id", sandbox_id, what="stop")


def guard(owner_pid: int, sandbox_id: str) -> None:
    """Wait for the owner process to end, however it ends, then stop its
    sandbox if that is still running. Runs as `asc _sandbox_guard`."""
    winapi.wait_exit(owner_pid)
    if sandbox_id in running():
        stop(sandbox_id)


def start_guard(owner_pid: int, sandbox_id: str, err: Path) -> subprocess.Popen[bytes]:
    with err.open("ab") as stream:
        command = spawn.asc_command("_sandbox_guard", str(owner_pid), sandbox_id)
        return spawn.detached(command, stream)
