from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    # An agent running the tests may have its own ASC_INSTANCE set.
    monkeypatch.delenv("ASC_INSTANCE", raising=False)
