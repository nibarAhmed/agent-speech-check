from __future__ import annotations

from pathlib import Path
from types import TracebackType

import pytest

from agent_speech_check import relay
from agent_speech_check.errors import ProtocolError, ServerSentError
from agent_speech_check.speech_log import SpeechLog


def _depth(tb: TracebackType | None) -> int:
    depth = 0
    while tb is not None:
        depth += 1
        tb = tb.tb_next
    return depth


def test_a_kept_protocol_error_does_not_grow_each_time_it_is_raised(tmp_path: Path) -> None:
    r = relay.Relay("d", relay.load_or_create(tmp_path), SpeechLog())
    r._lost(ServerSentError("boom"))
    depths = []
    for _ in range(3):
        with pytest.raises(ProtocolError, match="NVDA reported an error") as caught:
            r.check(0, drop_fails=False)
        depths.append(_depth(caught.value.__traceback__))
    assert depths[0] == depths[1] == depths[2]
