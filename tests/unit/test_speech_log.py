import asyncio
import re
import time

import pytest

from agent_speech_check.remote.speech_log import SpeechLog

pytestmark = pytest.mark.asyncio


async def test_numbering_and_lines() -> None:
    log = SpeechLog()
    log.said("Recycle Bin list item")
    log.said('say "hi"')
    since = log.since(0)
    assert [e.n for e in since.entries] == [1, 2]
    assert since.entries[0].line() == 'said: "Recycle Bin list item"'
    assert since.entries[1].line() == 'said: "say \\"hi\\""'
    assert log.latest == 2
    assert len(log) == 2


async def test_empty_speech_is_only_activity() -> None:
    log = SpeechLog()
    before = log.last_activity
    # Wait for the clock to move, not a fixed time: on Python 3.12 it ticks every 15.6 ms.
    while time.monotonic() <= before:
        await asyncio.sleep(0)
    log.said("")
    assert log.latest == 0
    assert log.last_activity > before


async def test_mark_and_clear_keep_numbers() -> None:
    log = SpeechLog()
    log.said("a")
    assert log.set_mark() == 1
    log.said("b")
    assert [e.text for e in log.since(log.mark).entries] == ["b"]
    log.clear()
    assert log.since(0).entries == ()
    assert log.since(0).dropped == 0
    log.said("c")
    assert log.since(0).entries[0].n == 3


async def test_cap_reports_dropped() -> None:
    log = SpeechLog(cap=3)
    for word in "abcde":
        log.said(word)
    since = log.since(0)
    assert [e.text for e in since.entries] == ["c", "d", "e"]
    assert since.dropped == 2
    assert log.since(1).dropped == 1
    assert log.since(3).dropped == 0


async def test_mode_from_sounds_and_words() -> None:
    log = SpeechLog()
    assert log.mode == "unknown"
    log.sound("focusMode.wav")
    assert log.mode == "focus"
    log.sound("error.wav")
    assert log.mode == "focus"
    log.said("Browse mode")
    assert log.mode == "browse"
    lines = [e.line() for e in log.since(0).entries]
    assert lines == ["mode: focus", 'said: "Browse mode"', "mode: browse"]


async def test_wait_match_returns_as_soon_as_words_arrive() -> None:
    log = SpeechLog()
    log.said("old OK")
    after = log.set_mark()

    async def speak_later() -> None:
        await asyncio.sleep(0.05)
        log.said("Cancel button")
        await asyncio.sleep(0.05)
        log.said("OK button")

    task = asyncio.create_task(speak_later())
    started = time.monotonic()
    entry = await log.wait_match(re.compile("OK"), after, timeout_s=5)
    await task
    assert entry is not None
    assert entry.text == "OK button"
    assert time.monotonic() - started < 1


async def test_wait_match_times_out() -> None:
    log = SpeechLog()
    log.sound("focusMode.wav")
    assert await log.wait_match(re.compile("focus"), 0, timeout_s=0.05) is None


async def test_wait_quiet() -> None:
    log = SpeechLog()
    log.activity()
    started = time.monotonic()
    assert await log.wait_quiet(0.05, timeout_s=2)
    assert time.monotonic() - started >= 0.04

    async def chatter() -> None:
        for _ in range(10):
            await asyncio.sleep(0.02)
            log.activity()

    task = asyncio.create_task(chatter())
    assert not await log.wait_quiet(0.1, timeout_s=0.1)
    await task
