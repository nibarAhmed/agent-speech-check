"""The one place keys are sent from, and the one owner of which keys may be held.

`Keyboard.press` refuses keys for targets that may not send them (watch mode),
holds one lock across each gesture and its settle period so two `asc press`
calls never interleave, and always releases every key it pressed.

NVDA keeps keys held even across reconnects (observed), so a key pressed on a
lost connection is released when NVDA joins again. Releases don't take the
lock, which a press holds through its settle period: a gesture never carries
on into a new connection, and a new press waits until the releases on a join
are done, so a release can't land in the middle of a gesture.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from agent_speech_check.errors import KeysBusy, KeysRefused, NvdaNotConnected
from agent_speech_check.keys import Gesture, Key
from agent_speech_check.relay import Relay
from agent_speech_check.speech_log import Entry, SpeechLog
from agent_speech_check.targets import Target


@dataclass(frozen=True)
class Pressed:
    entries: tuple[Entry, ...]
    matched: bool


class Keyboard:
    def __init__(self, target: Target, relay: Relay, speech: SpeechLog) -> None:
        self.target = target
        self.relay = relay
        self.speech = speech
        self._lock = asyncio.Lock()
        self._closed = False
        # Keys that may still be down in NVDA.
        self._maybe_held: set[Key] = set()
        # Keys pressed on the current connection. A release written just before
        # a drop may never arrive, so on the next join they all count as maybe held.
        self._pressed_here: set[Key] = set()
        # Clear while `joined` releases keys; presses wait for it.
        self._released = asyncio.Event()
        self._released.set()

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
            raise KeysRefused(self.target.name)
        if not self.relay.connected:
            raise NvdaNotConnected(self.target.name)
        deadline = time.monotonic() + timeout_s
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout_s)
        except TimeoutError:
            raise KeysBusy(int(timeout_s * 1000)) from None
        try:
            try:
                await asyncio.wait_for(self._released.wait(), deadline - time.monotonic())
            except TimeoutError:
                by = "releasing keys after NVDA reconnected"
                raise KeysBusy(int(timeout_s * 1000), by) from None
            start = self.speech.latest
            since = len(self.relay.losses)

            def check() -> None:
                # Fail if NVDA was dropped while this press ran, even if it came back.
                self.relay.check(since, drop_fails=True)

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
            return Pressed(self.speech.since(start).entries, matched)
        finally:
            self._lock.release()

    async def _send(self, gesture: Gesture, check: Callable[[], None]) -> None:
        """Key-downs in order, then releases in reverse, released even on failure."""
        try:
            for key in gesture.strokes:
                if self._closed:
                    # Shutting down: release_all already ran, so press nothing new.
                    raise NvdaNotConnected(self.target.name)
                check()
                self._maybe_held.add(key)
                self._pressed_here.add(key)
                await self.relay.send_key(key, pressed=True)
        finally:
            for key in reversed(gesture.strokes):
                if key in self._maybe_held:
                    # NVDA gone: the key stays maybe held and is released on rejoin.
                    with contextlib.suppress(NvdaNotConnected):
                        await self._release(key)
            self.speech.activity()

    async def _release(self, key: Key) -> None:
        await self.relay.send_key(key, pressed=False)
        self._maybe_held.discard(key)

    async def joined(self) -> None:
        """NVDA joined: release every key a lost connection may have left down."""
        self._maybe_held |= self._pressed_here
        self._pressed_here.clear()
        self._released.clear()
        try:
            for key in sorted(self._maybe_held, key=lambda k: k.vk):
                with contextlib.suppress(NvdaNotConnected):
                    await self._release(key)
        finally:
            self._released.set()

    async def release_all(self) -> None:
        """Release anything that may still be held, and press nothing after. Used at shutdown."""
        self._closed = True
        for key in list(self._maybe_held):
            with contextlib.suppress(NvdaNotConnected):
                await self._release(key)
