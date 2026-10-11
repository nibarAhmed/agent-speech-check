"""Shared checks for every edge where data enters `asc`.

Each edge (CLI arguments, IPC requests, wire messages, `targets.toml`, the state
file) parses raw input into frozen dataclasses through `Fields`, so every edge
reports a bad field the same way: `<where> <field>: expected ..., got ...`.

The caller picks the error class: `UsageError` for user input, `ProtocolError`
for wire data, `NotConnected` for a state file that `asc` wrote itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agent_speech_check.errors import AscError, bad_field

type ErrorClass = type[AscError]


class Fields:
    """Typed reads from one raw object, each failing with a field path."""

    def __init__(
        self,
        raw: object,
        where: str,
        error: ErrorClass,
        allowed: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(raw, Mapping) or not all(isinstance(k, str) for k in raw):
            raise bad_field(error, where, "an object", raw)
        self.raw: Mapping[str, Any] = raw
        self.where = where
        self.error = error
        if allowed is not None:
            unknown = sorted(set(raw) - set(allowed))
            if unknown:
                raise bad_field(error, where, "only known keys", ", ".join(unknown), quote=False)

    def _bad(self, key: str, expected: str) -> AscError:
        return bad_field(self.error, f"{self.where} {key}", expected, self.raw.get(key))

    def has(self, key: str) -> bool:
        return self.raw.get(key) is not None

    def text(self, key: str, *, max_len: int = 4096, allow_empty: bool = False) -> str:
        value = self.raw.get(key)
        if not isinstance(value, str) or (not value and not allow_empty) or len(value) > max_len:
            raise self._bad(key, f"a string of 1 to {max_len} characters")
        return value

    def opt_text(self, key: str, *, max_len: int = 4096) -> str | None:
        return self.text(key, max_len=max_len) if self.has(key) else None

    def number(self, key: str, *, low: int, high: int) -> int:
        value = self.raw.get(key)
        # bool is an int subclass, and True is never a valid number here.
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise self._bad(key, f"a whole number from {low} to {high}")
        return value

    def opt_number(self, key: str, *, low: int, high: int) -> int | None:
        return self.number(key, low=low, high=high) if self.has(key) else None

    def real(self, key: str) -> float:
        value = self.raw.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self._bad(key, "a number")
        return float(value)

    def flag(self, key: str) -> bool:
        value = self.raw.get(key)
        if not isinstance(value, bool):
            raise self._bad(key, "true or false")
        return value

    def choice(self, key: str, choices: Iterable[str]) -> str:
        options = tuple(choices)
        value = self.raw.get(key)
        if not isinstance(value, str) or value not in options:
            raise self._bad(key, f"one of {', '.join(options)}")
        return value

    def items(self, key: str) -> list[Any]:
        value = self.raw.get(key)
        if not isinstance(value, list):
            raise self._bad(key, "a list")
        return value
