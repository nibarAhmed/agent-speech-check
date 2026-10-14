from __future__ import annotations

import ctypes
from ctypes import wintypes

import pytest

from agent_speech_check import mouse


def pointer() -> tuple[int, int]:
    point = wintypes.POINT()
    assert ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


@pytest.mark.xdist_group("desktop")
def test_nudge_wins_the_foreground_without_moving_the_pointer() -> None:
    before = pointer()
    assert mouse.nudge()
    assert pointer() == before
