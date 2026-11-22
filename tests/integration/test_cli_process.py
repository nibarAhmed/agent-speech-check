"""Run the real `asc` entry point in a subprocess and check exit codes and streams."""

import subprocess
import sys


def run_asc(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "agent_speech_check", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_no_command_exits_2_on_stderr() -> None:
    result = run_asc()
    assert result.returncode == 2
    assert result.stdout == ""
    assert "error: no command given" in result.stderr
