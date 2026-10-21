import pytest

from agent_speech_check.daemon import ipc
from agent_speech_check.errors import UsageError


@pytest.mark.parametrize(
    ("cmd", "raw"),
    [("speech", {}), ("wait", {"pattern": "OK", "timeout_ms": 1000})],
)
def test_reading_speech_needs_since(cmd: str, raw: dict[str, object]) -> None:
    # The daemon keeps no position of its own, so each caller names one.
    with pytest.raises(UsageError, match="since"):
        ipc.parse_request(cmd, raw)
