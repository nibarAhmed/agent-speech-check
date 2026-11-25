from __future__ import annotations

import sys

import pytest

# A test marked with an OS runs only there, and is skipped elsewhere.
PLATFORMS = {"windows": "win32", "linux": "linux"}


def pytest_runtest_setup(item: pytest.Item) -> None:
    for marker, platform in PLATFORMS.items():
        if item.get_closest_marker(marker) and sys.platform != platform:
            pytest.skip(f"needs {marker.capitalize()}")


@pytest.fixture(autouse=True)
def _default_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    # An agent running the tests may have its own ASC_INSTANCE set.
    monkeypatch.delenv("ASC_INSTANCE", raising=False)
