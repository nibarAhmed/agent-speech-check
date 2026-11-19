"""The one place keys are sent from, and the one owner of which keys may be held.

`Keyboard.press` refuses keys in watch mode, which may not send them,
holds one lock across each gesture and its settle period so two `asc press`
calls never interleave, and always releases every key it pressed.

NVDA keeps keys held even across reconnects (observed), so every key pressed
since NVDA last joined is released again when it next joins. Releases only
queue a write and never await, so they need no lock: nothing else can run
between two of them, and a gesture never carries on into a new connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from agent_speech_check.config.targets import Target
from agent_speech_check.errors import KeysBusy, KeysRefused, NvdaNotConnected
from agent_speech_check.inputs.keys import Gesture, Key
from agent_speech_check.remote.speech_log import Since, SpeechLog


class Link(Protocol):
    """What keys go through: the relay NVDA joins, or Orca's helper."""

    on_join: Callable[[], None]
    losses: list[str | None]

    @property
    def connected(self) -> bool: ...

    @property
    def not_connected(self) -> type[NvdaNotConnected]: ...

    def check(self, since: int, *, drop_fails: bool) -> None: ...

    def write_key(self, key: Key, *, pressed: bool) -> object: ...

    async def send_key(self, key: Key, *, pressed: bool) -> None: ...


@dataclass(frozen=True)
class Pressed:
    since: Since
    matched: bool
    after: int  # the latest line before the first key


class Keyboard:
    def __init__(self, target: Target, link: Link, speech: SpeechLog) -> None:
        self.target = target
        self.link = link
        self.speech = speech
        self._lock = asyncio.Lock()
        self._closed = False
        # Keys pressed since NVDA last joined. A release written just before a
        # drop may never arrive, so all of them are released on the next join.
        self._pressed: set[Key] = set()
        link.on_join = self.joined

    async def press(
        self,
        gestures: tuple[Gesture, ...],
        settle_s: float,
        timeout_s: float,
        expect: re.Pattern[str] | None = None,
        *,
        settle_each: bool = True,
    ) -> Pressed:
        """Send the gestures and collect what NVDA said.

        With `settle_each` (asc press) NVDA gets a quiet period after every
        gesture; without it (asc type) only after the last.
        """
        if not self.target.keys:
            raise KeysRefused
        if not self.link.connected:
            raise self.link.not_connected
        deadline = time.monotonic() + timeout_s
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout_s)
        except TimeoutError:
            raise KeysBusy(int(timeout_s * 1000)) from None
        try:
            start = self.speech.latest
            # Fail if NVDA was dropped while this press ran, even if it came back.
            check = functools.partial(self.link.check, len(self.link.losses), drop_fails=True)

            for index, gesture in enumerate(gestures):
                await self._send(gesture, check)
                if settle_each or index == len(gestures) - 1:
                    remaining = max(0.0, deadline - time.monotonic())
                    await self.speech.wait_quiet(settle_s, remaining)
                check()
            matched = True
            if expect is not None:
                remaining = max(0.0, deadline - time.monotonic())
                found = await self.speech.wait_match(expect, start, remaining, check)
                matched = found is not None
            return Pressed(self.speech.since(start), matched, start)
        finally:
            self._lock.release()

    async def _send(self, gesture: Gesture, check: Callable[[], None]) -> None:
        """Key-downs in order, then releases in reverse, released even on failure."""
        down: list[Key] = []
        try:
            for key in gesture.strokes:
                if self._closed:
                    # Shutting down: release_all already ran, so press nothing new.
                    raise self.link.not_connected
                check()
                self._pressed.add(key)
                down.append(key)
                await self.link.send_key(key, pressed=True)
        finally:
            self._release(reversed(down))
            self.speech.activity()

    def _release(self, keys: Iterable[Key]) -> None:
        for key in keys:
            # NVDA gone: the key stays in `_pressed` and is released on rejoin.
            with contextlib.suppress(NvdaNotConnected):
                self.link.write_key(key, pressed=False)

    def joined(self) -> None:
        """NVDA joined: release every key a lost connection may have left down."""
        self._release(sorted(self._pressed, key=lambda k: k.vk))
        self._pressed.clear()

    def release_all(self) -> None:
        """Release anything that may still be held, and press nothing after. Used at shutdown."""
        self._closed = True
        self._release(self._pressed)
