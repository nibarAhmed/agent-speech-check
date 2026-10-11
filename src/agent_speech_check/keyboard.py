"""The one place keys are sent from.

`Keyboard.press` refuses keys for targets that may not send them (watch mode),
holds one lock across each gesture and its settle period so two `asc press`
calls never interleave, and always releases every key it pressed.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass

from agent_speech_check.errors import KeysBusy, KeysRefused, NvdaNotConnected
from agent_speech_check.keys import Gesture
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

    async def press(
        self,
        gestures: tuple[Gesture, ...],
        settle_s: float,
        timeout_s: float,
        expect: re.Pattern[str] | None = None,
    ) -> Pressed:
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
            start = self.speech.latest
            failures, drops = self.relay.failures, self.relay.drops
            for gesture in gestures:
                await self._send(gesture)
                await self.speech.wait_quiet(settle_s, max(0.0, deadline - time.monotonic()))
                self._check(failures, drops)
            matched = True
            if expect is not None:
                remaining = max(0.0, deadline - time.monotonic())
                found = await self.speech.wait_match(
                    expect, start, remaining, lambda: self._check(failures, drops)
                )
                matched = found is not None
            return Pressed(self.speech.since(start).entries, matched)
        finally:
            self._lock.release()

    def _check(self, failures: int, drops: int) -> None:
        """Fail if NVDA was dropped while this press ran, even if it came back."""
        self.relay.raise_failure_since(failures)
        if not self.relay.connected or self.relay.drops != drops:
            raise NvdaNotConnected(self.target.name)

    async def _send(self, gesture: Gesture) -> None:
        """Key-downs in order, then releases in reverse, released even on failure."""
        try:
            for key in gesture.strokes:
                await self.relay.send_key(key, pressed=True)
        finally:
            for key in reversed(gesture.strokes):
                if key in self.relay.maybe_held:
                    # NVDA gone: the key stays in maybe_held and is released on rejoin.
                    with contextlib.suppress(NvdaNotConnected):
                        await self.relay.send_key(key, pressed=False)
            self.speech.activity()

    async def release_all(self) -> None:
        """Release anything that may still be held. Used at shutdown."""
        for key in list(self.relay.maybe_held):
            with contextlib.suppress(NvdaNotConnected):
                await self.relay.send_key(key, pressed=False)
