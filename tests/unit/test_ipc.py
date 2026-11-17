from pathlib import Path

import pytest

from agent_speech_check.config.targets import defaults
from agent_speech_check.daemon import ipc, server
from agent_speech_check.errors import UsageError


def test_the_daemon_handles_every_command_the_client_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    daemon = server.Daemon(defaults("watch"), tmp_path)
    assert daemon.handlers.keys() == ipc.COMMANDS.keys()


@pytest.mark.parametrize(
    ("cmd", "raw"),
    [("speech", {}), ("wait", {"pattern": "OK", "timeout_ms": 1000})],
)
def test_reading_speech_needs_since(cmd: str, raw: dict[str, object]) -> None:
    # The daemon keeps no position of its own, so each caller names one.
    with pytest.raises(UsageError, match="since"):
        ipc.parse_request(cmd, raw)
