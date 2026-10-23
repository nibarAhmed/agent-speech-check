"""The Claude Code and Codex plugin manifests agree with each other and with pyproject.toml.

Both tools update an installed plugin only when its version changes, so a release that forgets
a manifest leaves users on the old skill.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN = ROOT / "plugins" / "agent-speech-check"
MANIFESTS = [PLUGIN / ".claude-plugin" / "plugin.json", PLUGIN / ".codex-plugin" / "plugin.json"]
MARKETPLACES = [
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".agents" / "plugins" / "marketplace.json",
]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def source_path(entry: dict[str, Any]) -> str:
    """Claude Code writes a local source as a string, Codex as {"source": "local", "path": ...}."""
    source = entry["source"]
    if isinstance(source, str):
        return source
    assert source["source"] == "local"
    path: str = source["path"]
    return path


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda path: path.parent.name)
def test_plugin_version_is_the_package_version(manifest: Path) -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert load(manifest)["version"] == pyproject["project"]["version"]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda path: path.parent.name)
def test_plugin_name_is_the_folder_name(manifest: Path) -> None:
    assert load(manifest)["name"] == PLUGIN.name


@pytest.mark.parametrize("marketplace", MARKETPLACES, ids=lambda path: path.parent.name)
def test_marketplace_lists_the_plugin_by_its_name_and_folder(marketplace: Path) -> None:
    [entry] = load(marketplace)["plugins"]
    assert entry["name"] == PLUGIN.name
    assert (ROOT / source_path(entry)).resolve() == PLUGIN


def test_plugin_holds_the_asc_skill() -> None:
    assert (PLUGIN / "skills" / "asc" / "SKILL.md").is_file()
