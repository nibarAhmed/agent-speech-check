"""`asc _stop_all`, how the installer stops every daemon before it replaces or removes asc, and
`asc stop --all`, how a person does before deleting it on Linux."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.integration.conftest import Env, free_port, run_asc, start_asc, wait_until

from agent_speech_check.config.targets import Target
from agent_speech_check.daemon import session
from agent_speech_check.errors import OperationalError
from agent_speech_check.system import processes


def _pid(env: Env, folder: str) -> int:
    pid: int = json.loads((env.folder(folder) / "state.json").read_text(encoding="utf-8"))["pid"]
    return pid


def _silent_daemon(env: Env, name: str) -> int:
    """A process that holds a port and drops every request, with a state file in
    a folder of its own: what a stuck daemon of an older asc looks like."""
    port = free_port()
    serve = (
        "import socket\n"
        f"server = socket.create_server(('127.0.0.1', {port}))\n"
        "while True:\n"
        "    server.accept()[0].close()\n"
    )
    process = subprocess.Popen([sys.executable, "-c", serve])  # noqa: S603 - our own interpreter
    assert wait_until(lambda: _listening(port))
    folder = env.folder(name)
    folder.mkdir(parents=True)
    state = {"pid": process.pid, "port": port, "token": "t", "started": time.time()}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return process.pid


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return False
    return True


@pytest.mark.parametrize("command", [["_stop_all"], ["stop", "--all"]])
def test_stop_all_stops_every_daemon(
    env: Env, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    assert run_asc("start", "--mode", "watch").returncode == 0
    # And a named instance's.
    monkeypatch.setenv("ASC_INSTANCE", "vm1")
    vm1_daemon = start_asc("_daemon", Target("watch", free_port(), os="windows").to_json())
    monkeypatch.delenv("ASC_INSTANCE")
    assert wait_until(lambda: session.is_running(env.folder("watch", "vm1")))
    # One that doesn't answer, as an older asc's daemon wouldn't.
    pids = [_pid(env, "watch"), _silent_daemon(env, "old-target")]
    # And a file no daemon of this asc wrote.
    old = env.folder("old")
    old.mkdir()
    (old / "state.json").write_text('{"pid": "old"}', encoding="utf-8")
    # And one left by a daemon that died: nothing listens on its port.
    dead = env.folder("dead")
    dead.mkdir()
    stale = {"pid": 1, "port": free_port(), "token": "old", "started": 0}
    (dead / "state.json").write_text(json.dumps(stale), encoding="utf-8")

    try:
        result = run_asc(*command)
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "stopped: old-target\nstopped: watch\nstopped: instances/vm1/watch\n"
        )
        # Run from source it has no asc.exe to wait for, so it may return a moment early.
        assert wait_until(lambda: [processes.image_path(pid) for pid in pids] == [None, None])
        assert list(env.root.glob("agent-speech-check/**/state.json")) == []
    finally:
        if vm1_daemon.poll() is None:
            vm1_daemon.kill()
        vm1_daemon.wait(timeout=15)


@pytest.mark.usefixtures("env")
def test_stop_all_with_nothing_running_does_nothing() -> None:
    result = run_asc("_stop_all")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def _stand_in(tmp_path: Path, seconds: int) -> tuple[Path, subprocess.Popen[bytes]]:
    """A process running its own copy of an exe, like another asc.exe still running."""
    image = tmp_path / "stand-in.exe"
    shutil.copy(Path(shutil.which("ping") or "ping.exe"), image)
    process = subprocess.Popen(  # noqa: S603 - our copy of ping, fixed arguments
        [str(image), "-n", str(seconds + 1), "127.0.0.1"], stdout=subprocess.DEVNULL
    )
    return image, process


def test_stop_all_waits_for_other_processes_of_the_same_exe(env: Env, tmp_path: Path) -> None:
    image, process = _stand_in(tmp_path, 2)
    try:
        assert list(session.stop_all([env.root / "agent-speech-check"], [image])) == []
        assert process.poll() is not None
    finally:
        process.kill()


def test_stop_all_names_processes_that_stay(env: Env, tmp_path: Path) -> None:
    image, process = _stand_in(tmp_path, 60)
    try:
        with pytest.raises(OperationalError, match=rf"asc is still running \(pid {process.pid}\)"):
            list(session.stop_all([env.root / "agent-speech-check"], [image], timeout_s=0.5))
        # Only waited on: never ended.
        assert process.poll() is None
    finally:
        process.kill()
