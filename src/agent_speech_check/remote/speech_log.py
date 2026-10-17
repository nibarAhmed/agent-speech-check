"""What NVDA said, numbered and timestamped, in memory only.

Lines are numbered from 1 and numbers are never reused, even after `clear`. The
log keeps the newest `CAP` lines. It also tracks browse/focus mode from NVDA's
mode sounds (`focusMode.wav`, `browseMode.wav`) or, when the user turned those
sounds off, from the spoken words "focus mode" and "browse mode".

All methods run on the daemon's event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from agent_speech_check.errors import said_line

CAP = 10_000
MODE_SOUNDS = {"focusmode.wav": "focus", "browsemode.wav": "browse"}
MODE_WORDS = {"focus mode": "focus", "browse mode": "browse"}

type Kind = Literal["said", "mode"]


@dataclass(frozen=True)
class Entry:
    n: int
    ms: int
    kind: Kind
    text: str

    def line(self) -> str:
        """The line as `asc` prints it, without the number."""
        if self.kind == "mode":
            return f"mode: {self.text}"
        return said_line(self.text)


@dataclass(frozen=True)
class Since:
    entries: tuple[Entry, ...]
    dropped: int


class SpeechLog:
    def __init__(self, cap: int = CAP) -> None:
        self._entries: deque[Entry] = deque(maxlen=cap)
        self._next = 1
        self._start = time.monotonic()
        # Lines below this number fell off the end of the log (not cleared).
        self._dropped_below = 1
        self._changed = asyncio.Event()
        self.mark = 0
        self.mode = "unknown"
        self.last_activity = self._start

    @property
    def latest(self) -> int:
        return self._next - 1

    def __len__(self) -> int:
        return len(self._entries)

    def _add(self, kind: Kind, text: str) -> Entry:
        if self._entries.maxlen is not None and len(self._entries) == self._entries.maxlen:
            self._dropped_below = self._entries[0].n + 1
        entry = Entry(self._next, int((time.monotonic() - self._start) * 1000), kind, text)
        self._next += 1
        self._entries.append(entry)
        self._wake()
        return entry

    def _wake(self) -> None:
        self.last_activity = time.monotonic()
        self._changed.set()
        self._changed = asyncio.Event()

    def activity(self) -> None:
        """Any message from NVDA, spoken or not, delays the quiet period."""
        self._wake()

    def said(self, text: str) -> None:
        if not text:
            self.activity()
            return
        self._add("said", text)
        mode = MODE_WORDS.get(text.strip().lower())
        if mode is not None:
            self._set_mode(mode)

    def sound(self, name: str) -> None:
        mode = MODE_SOUNDS.get(name.lower())
        if mode is None:
            self.activity()
        else:
            self._set_mode(mode)

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._add("mode", mode)

    def set_mark(self) -> int:
        self.mark = self.latest
        return self.mark

    def since(self, n: int) -> Since:
        """Lines numbered above `n`, and how many of those fell off the log."""
        # Numbers are consecutive within the log, so the first kept line
        # above `n` is found by arithmetic instead of a scan.
        first = self._entries[0].n if self._entries else self._next
        count = len(self._entries) - min(max(0, n + 1 - first), len(self._entries))
        entries = tuple(itertools.islice(reversed(self._entries), count))[::-1]
        dropped = max(0, self._dropped_below - 1 - n)
        return Since(entries, dropped)

    async def wait_match(
        self,
        pattern: re.Pattern[str],
        after: int,
        timeout_s: float,
        check: Callable[[], None] = lambda: None,
    ) -> Entry | None:
        """The first spoken line above `after` that matches, or None at the timeout.

        `check` runs on every wake-up and may raise to stop waiting.
        """
        deadline = time.monotonic() + timeout_s
        checked = after
        while True:
            check()
            for entry in self.since(checked).entries:
                checked = entry.n
                if entry.kind == "said" and pattern.search(entry.text):
                    return entry
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await self._wait_change(remaining)

    async def wait_quiet(self, settle_s: float, timeout_s: float) -> bool:
        """Wait until NVDA has been quiet for `settle_s`. False if the timeout came first."""
        deadline = time.monotonic() + timeout_s
        while True:
            now = time.monotonic()
            quiet_at = self.last_activity + settle_s
            if now >= quiet_at:
                return True
            if now >= deadline:
                return False
            await self._wait_change(min(quiet_at, deadline) - now)

    async def _wait_change(self, timeout_s: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._changed.wait(), timeout_s)
