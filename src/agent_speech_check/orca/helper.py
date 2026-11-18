"""Runs on the Linux machine, local or over SSH: a private X display with its own Orca.

asc sends this file over stdin to `python3 -I -c BOOTSTRAP` (see `link.py`), so
it uses only the standard library and runs on any Python 3.9 or later. It
writes no file but a temporary folder, removed when it ends.

Over stdin and stdout, one JSON object per line. In:
- `{"key": "Insert", "down": true, "id": 1}`: an X keysym through xdotool; `id` asks for `done`
- `{"launch": "gedit --new-window", "id": 2}`: start an app on the display
Out:
- `{"ready": "51.1"}`: Orca runs and its speech comes next
- `{"record": {...}}`: one line of Orca's speech recording
- `{"done": 1}`, or `{"done": 2, "error": "..."}`
- `{"failed": "setup" or "busy", "reason": "..."}`, then it exits

Stdin closing, SIGTERM or SIGHUP ends it, and everything it started with it.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO, Any

PROGRAMS = ("Xvfb", "openbox", "xdotool", "dbus-daemon", "gdbus", "orca")
MIN_ORCA = (51, 0)
READY_TIMEOUT_S = 30.0
SERVICE = "org.gnome.Orca1.Service"
XDOTOOL_TIMEOUT_S = 10.0


def orca_version(text: str) -> tuple[int, int] | None:
    """`orca --version` prints `Orca screen reader version 51.1`, or so."""
    found = re.search(r"(\d+)\.(\d+)", text)
    return None if found is None else (int(found[1]), int(found[2]))


def others_running(uid: int, proc: Path = Path("/proc")) -> bool:
    """Whether this user runs an Orca already, which a new Orca refuses to start beside."""
    for folder in proc.glob("[0-9]*"):
        try:
            comm = (folder / "comm").read_text().strip()
            owner = folder.stat().st_uid
        except OSError:
            continue
        if comm == "orca" and owner == uid:
            return True
    return False


def children_of(pid: int, proc: Path = Path("/proc")) -> list[int]:
    """Its child processes, from `/proc/N/stat`: `N (comm) state ppid ...`."""
    found = []
    for stat in proc.glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[1]) == pid:
            found.append(int(stat.parent.name))
    return found


def remove_stale(temp: Path, uid: int, proc: Path = Path("/proc")) -> None:
    """Folders this user's helpers left when killed, which no `close` removed."""
    for folder in temp.glob("asc-orca-*"):
        try:
            if folder.stat().st_uid != uid:
                continue
            pid = (folder / "pid").read_text().strip()
        except OSError:
            continue
        if pid.isdigit() and not (proc / pid).exists():
            shutil.rmtree(folder, ignore_errors=True)


class Failed(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


class Output:
    """Stdout, one whole line at a time from either thread."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message).encode() + b"\n"
        with self._lock:
            try:
                self._stream.write(line)
                self._stream.flush()
            except (BrokenPipeError, ValueError):
                pass  # asc is gone; stdin closing ends this too


class Session:
    def __init__(self, out: Output) -> None:
        self.out = out
        remove_stale(Path(tempfile.gettempdir()), os.getuid())
        self.folder = Path(tempfile.mkdtemp(prefix="asc-orca-"))
        (self.folder / "pid").write_text(str(os.getpid()))
        self.children: list[subprocess.Popen[bytes]] = []
        self.apps: list[subprocess.Popen[bytes]] = []
        self.xvfb: subprocess.Popen[bytes] | None = None
        self.env = dict(os.environ)
        # Looked up before any fork: the child may only call it.
        prctl = ctypes.CDLL(None).prctl
        pr_set_pdeathsig, pr_set_child_subreaper = 1, 36
        self._die_with_parent = lambda: prctl(pr_set_pdeathsig, signal.SIGKILL)
        # What leaves its group, like a daemon an app starts, becomes our child, for `close`.
        prctl(pr_set_child_subreaper, 1)

    def start(self, args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        """Its own session, so teardown ends its whole group, and SIGKILL if this ends."""
        kwargs.setdefault("stdout", subprocess.DEVNULL)
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.env,
            start_new_session=True,
            preexec_fn=self._die_with_parent,  # noqa: PLW1509 - nothing but prctl runs there
            **kwargs,
        )
        self.children.append(process)
        return process

    def setup(self) -> str:
        missing = [name for name in PROGRAMS if shutil.which(name) is None]
        if missing:
            raise Failed("setup", "missing programs: " + ", ".join(missing))
        try:
            found = subprocess.run(
                ["orca", "--version"], capture_output=True, text=True, check=False, timeout=30
            ).stdout
        except subprocess.TimeoutExpired:
            raise Failed("setup", "orca --version did not answer within 30 s") from None
        version = orca_version(found)
        if version is None or version < MIN_ORCA:
            raise Failed("setup", f"needs Orca 51 or later, found {found.strip()!r}")
        if others_running(os.getuid()):
            raise Failed("busy", "this user already runs Orca, and Orca runs once per user")
        secret = secrets.token_hex(16)
        # First, so the AT-SPI bus that dbus-daemon starts gets the private folders too.
        self._environment(secret)
        self._display()
        self._bus()
        self.start(["openbox"])
        self.orca = self.start(["orca"])
        self._wait_orca()
        self._record(secret)
        return "{}.{}".format(*version)

    def _display(self) -> None:
        read, write = os.pipe()
        self.xvfb = self.start(
            ["Xvfb", "-displayfd", str(write), "-noreset", "-nolisten", "tcp"],
            pass_fds=(write,),
        )
        self.children.remove(self.xvfb)  # ended last, after everything that draws on it
        os.close(write)
        with os.fdopen(read) as pipe:
            number = pipe.readline().strip()
        if not number.isdigit():
            raise Failed("setup", "Xvfb did not start")
        self.env["DISPLAY"] = f":{number}"

    def _bus(self) -> None:
        bus = self.start(
            [
                *("dbus-daemon", "--session", "--nofork", "--print-address"),
                f"--address=unix:dir={self.folder}",
            ],
            stdout=subprocess.PIPE,
        )
        address = bus.stdout.readline().decode().strip() if bus.stdout else ""
        if not address:
            raise Failed("setup", "dbus-daemon did not start")
        self.env["DBUS_SESSION_BUS_ADDRESS"] = address

    def _environment(self, secret: str) -> None:
        home = self.folder / "home"
        runtime = self.folder / "run"
        home.mkdir()
        runtime.mkdir(mode=0o700)
        self.env.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local" / "share"),
            XDG_CACHE_HOME=str(home / ".cache"),
            XDG_STATE_HOME=str(home / ".local" / "state"),
            XDG_RUNTIME_DIR=str(runtime),
            GSETTINGS_BACKEND="memory",
            GTK_A11Y="atspi",
            NO_AT_BRIDGE="0",
            GDK_BACKEND="x11",
            ACCESSIBILITY_ENABLED="1",
            GNOME_ACCESSIBILITY="1",
            ORCA_TEST_RPC_SECRET=secret,
        )
        self.env.pop("WAYLAND_DISPLAY", None)
        self.env.setdefault("LANG", "C.UTF-8")

    def _gdbus(self, path: str, method: str, *args: str, timeout: float = 30) -> bool:
        command = ["gdbus", "call", "--session", "--dest", SERVICE, "--object-path", path]
        try:
            result = subprocess.run(
                [*command, "--method", method, *args],
                env=self.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _wait_orca(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        path = "/org/gnome/Orca1/Service"
        while not self._gdbus(
            path, f"{SERVICE}.GetVersion", timeout=max(0.1, deadline - time.monotonic())
        ):
            code = self.orca.poll()
            if code is not None:
                raise Failed("setup", f"Orca exited with code {code}")
            if time.monotonic() > deadline:
                raise Failed("setup", f"Orca did not answer within {READY_TIMEOUT_S:g} s")
            time.sleep(0.1)

    def _record(self, secret: str) -> None:
        fifo = self.folder / "speech"
        os.mkfifo(fifo, 0o600)
        # The reader first: Orca's open for writing waits until one exists.
        threading.Thread(target=self._forward, args=(fifo,), daemon=True).start()
        path = "/org/gnome/Orca1/Service/SpeechPresenter"
        method = "org.gnome.Orca1.SpeechPresenter.SetLogFileForTesting"
        if not self._gdbus(path, method, secret, str(fifo)):
            raise Failed("setup", "Orca refused to record its speech")

    def _forward(self, fifo: Path) -> None:
        with fifo.open("rb") as records:
            for line in records:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                self.out.send({"record": record})
        # Orca closed it: Orca has stopped, so this ends too.
        os.kill(os.getpid(), signal.SIGTERM)

    def handle(self, message: dict[str, Any]) -> None:
        ident = message.get("id")
        error = None
        try:
            if "key" in message:
                action = "keydown" if message.get("down") else "keyup"
                self._xdotool(action, str(message["key"]))
            elif "launch" in message:
                self._launch(str(message["launch"]))
        except (OSError, ValueError, subprocess.SubprocessError) as problem:
            error = str(problem) or type(problem).__name__
        if ident is not None:
            reply: dict[str, Any] = {"done": ident}
            if error is not None:
                reply["error"] = error
            self.out.send(reply)

    def _xdotool(self, action: str, keysym: str) -> None:
        subprocess.run(
            ["xdotool", action, keysym],
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=XDOTOOL_TIMEOUT_S,
        )

    def _launch(self, command: str) -> None:
        args = shlex.split(command)
        if not args:
            raise ValueError("no command")
        app = self.start(args)
        self.children.remove(app)
        self.apps.append(app)

    def close(self) -> None:
        # Ending Orca ends its recording, which sends SIGTERM: it must not cut this short.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        for process in [*self.apps, *reversed(self.children), *filter(None, [self.xvfb])]:
            _end_group(process)
        # Bounded: each round ends the orphans the last one left.
        for _ in range(100):
            orphans = children_of(os.getpid())
            if not orphans:
                break
            for pid in orphans:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            for pid in orphans:
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, 0)
        shutil.rmtree(self.folder, ignore_errors=True)


def _end_group(process: subprocess.Popen[bytes]) -> None:
    """Its whole group: dbus-daemon's group holds the AT-SPI bus it started."""
    with contextlib.suppress(ProcessLookupError, subprocess.TimeoutExpired):
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(2.0)
    # The leader may be gone while others in its group are not.
    with contextlib.suppress(ProcessLookupError, subprocess.TimeoutExpired):
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(2.0)


def _stop(*_: object) -> None:
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    out = Output(sys.stdout.buffer)
    session = Session(out)
    try:
        try:
            out.send({"ready": session.setup()})
        except Failed as failed:
            out.send({"failed": failed.kind, "reason": failed.reason})
            return
        for line in sys.stdin.buffer:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict):
                session.handle(message)
    finally:
        session.close()


if __name__ == "__main__":
    main()
