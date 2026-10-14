"""Waiting for something outside asc: a file, a process, a sandbox session."""

from __future__ import annotations

import time
from collections.abc import Callable


def poll_until(condition: Callable[[], bool], timeout_s: float, interval_s: float) -> bool:
    """Check `condition` every `interval_s` until it holds (True) or `timeout_s`
    has passed (False)."""
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_s)
    return True
