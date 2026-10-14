from __future__ import annotations

import itertools
import time

from agent_speech_check.wait import poll_until


def test_a_condition_that_holds_returns_at_once() -> None:
    started = time.monotonic()
    assert poll_until(lambda: True, 10, 5)
    assert time.monotonic() - started < 1


def test_a_condition_that_comes_true_later() -> None:
    calls = itertools.count(1)
    assert poll_until(lambda: next(calls) == 3, 10, 0.01)
    assert next(calls) == 4


def test_a_condition_that_never_holds_times_out() -> None:
    started = time.monotonic()
    assert not poll_until(lambda: False, 0.2, 0.01)
    assert 0.2 <= time.monotonic() - started < 2
