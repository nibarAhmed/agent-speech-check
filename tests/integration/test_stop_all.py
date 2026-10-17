"""`asc _stop_all`: how the installer stops every daemon before it replaces or removes asc."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.integration.conftest import Env, free_port, run_asc, wait_until

from agent_speech_check.daemon import session
from agent_speech_check.errors import AscStillRunning
from agent_speech_check.windows import winapi


def _pid(env: Env, target: str) -> int:
    pid: int = json.loads((env.folder(target) / "state.json").read_text(encoding="utf-8"))["pid"]
    return pid


def test_stop_all_stops_every_daemon(env: Env) -> None:
    assert run_asc("start", "--target", "w").returncode == 0
    # Its own relay port, picked once w holds its own: two daemons can't share one.
    config = env.root / "agent-speech-check" / "config.toml"
    with config.open("a", encoding="utf-8") as file:
        file.write(f'[targets.w2]\nmode = "watch"\nport = {free_port()}\n')
    started = run_asc("start", "--target", "w2")
    assert started.returncode == 0, started.stderr
    pids = [_pid(env, "w"), _pid(env, "w2")]
    # One that doesn't answer (a token it doesn't know), as an older asc wouldn't.
    path = env.folder("w2") / "state.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**current, "token": "not-its-token"}), encoding="utf-8")
    # And a file no daemon of this asc wrote.
    old = env.folder("old")
    old.mkdir()
    (old / "state.json").write_text('{"pid": "old"}', encoding="utf-8")
    # And one left by a daemon that died: nothing listens on its port.
    dead = env.folder("dead")
    dead.mkdir()
    stale = {"pid": 1, "port": free_port(), "token": "old", "started": 0}
    (dead / "state.json").write_text(json.dumps(stale), encoding="utf-8")

    result = run_asc("_stop_all")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "stopped: w\nstopped: w2\n"
    # Run from source it has no asc.exe to wait for, so it may return a moment early.
    assert wait_until(lambda: [winapi.image_path(pid) for pid in pids] == [None, None])
    assert list(env.root.glob("agent-speech-check/*/state.json")) == []


@pytest.mark.usefixtures("env")
def test_stop_all_with_nothing_running_does_nothing() -> None:
    result = run_asc("_stop_all")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def _stand_in(tmp_path: Path, seconds: int) -> tuple[Path, subprocess.Popen[bytes]]:
    """A process running its own copy of an exe, like a sandbox guard still running asc.exe."""
    image = tmp_path / "stand-in.exe"
    shutil.copy(Path(shutil.which("ping") or "ping.exe"), image)
    process = subprocess.Popen(  # noqa: S603 - our copy of ping, fixed arguments
        [str(image), "-n", str(seconds + 1), "127.0.0.1"], stdout=subprocess.DEVNULL
    )
    return image, process


def test_stop_all_waits_for_other_processes_of_the_same_exe(env: Env, tmp_path: Path) -> None:
    image, process = _stand_in(tmp_path, 2)
    try:
        assert list(session.stop_all(env.root / "agent-speech-check", image)) == []
        assert process.poll() is not None
    finally:
        process.kill()


def test_stop_all_names_processes_that_stay(env: Env, tmp_path: Path) -> None:
    image, process = _stand_in(tmp_path, 60)
    try:
        with pytest.raises(AscStillRunning, match=rf"asc is still running \(pid {process.pid}\)"):
            list(session.stop_all(env.root / "agent-speech-check", image, timeout_s=0.5))
        # Only waited on: never ended.
        assert process.poll() is None
    finally:
        process.kill()
