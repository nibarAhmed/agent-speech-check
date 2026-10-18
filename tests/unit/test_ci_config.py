from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_zizmor_workflow_runs_the_locked_zizmor() -> None:
    """Dependabot bumps zizmor in uv.lock but not the workflow's `version:`, so this
    fails until the two match again."""
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p["version"] for p in lock["package"] if p["name"] == "zizmor")
    workflow = (REPO / ".github" / "workflows" / "zizmor.yml").read_text(encoding="utf-8")
    pinned = re.search(r"^\s*version: (\S+)$", workflow, re.MULTILINE)
    assert pinned is not None
    assert pinned.group(1) == locked
