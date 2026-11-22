"""Uninstall an `asc` installed from a Linux release tarball."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check.errors import FileUnusable, OperationalError
from agent_speech_check.system import paths
from agent_speech_check.update import updater


@dataclass(frozen=True)
class Plan:
    exe: Path
    root: Path
    links: tuple[Path, ...]
    state: Path
    state_exists: bool
    has_orca_secret: bool


def _candidate_links(argv0: str, default_link: Path) -> list[Path]:
    candidates: list[Path] = []
    if argv0:
        if os.sep in argv0 or (os.altsep is not None and os.altsep in argv0):
            candidates.append(Path(argv0).absolute())
        elif found := shutil.which(argv0):
            candidates.append(Path(found).absolute())
    if found_asc := shutil.which("asc"):
        candidates.append(Path(found_asc).absolute())
    candidates.append(default_link.absolute())
    return candidates


def find_links(root: Path, argv0: str, default_link: Path | None = None) -> tuple[Path, ...]:
    """External `asc` symlinks pointing inside the install `root`."""
    resolved_root = root.resolve()
    home_link = default_link if default_link is not None else Path.home() / ".local" / "bin" / "asc"
    links: list[Path] = []
    seen: set[Path] = set()
    for candidate in _candidate_links(argv0, home_link):
        if not candidate.is_symlink():
            continue
        key = candidate.parent.resolve() / candidate.name
        if key in seen or key.is_relative_to(resolved_root):
            continue
        with contextlib.suppress(OSError):
            if candidate.resolve(strict=True).is_relative_to(resolved_root):
                seen.add(key)
                links.append(key)
    return tuple(links)


def plan(
    exe: Path | None = None,
    *,
    argv0: str | None = None,
    state: Path | None = None,
    default_link: Path | None = None,
) -> Plan:
    """Resolve what an uninstall will stop and remove, or refuse non-tarball installs."""
    installed = updater.installed_exe() if exe is None else exe.resolve()
    root = None if installed is None else updater.install_root(installed)
    if installed is None or root is None:
        raise OperationalError(
            "asc was not installed from a Linux release tarball",
            hint="remove it with the tool that installed it",
        )
    state_dir = paths.base_root() if state is None else state
    links = find_links(root, sys.argv[0] if argv0 is None else argv0, default_link)
    return Plan(
        exe=installed,
        root=root,
        links=links,
        state=state_dir,
        state_exists=state_dir.exists(),
        has_orca_secret=(state_dir / "orca-secret").exists(),
    )


def remove_files(planned: Plan, *, keep_data: bool) -> list[Path]:
    """Delete the symlinks, optionally the state directory, and finally the install root."""
    removed: list[Path] = []
    for link in planned.links:
        try:
            link.unlink(missing_ok=True)
        except OSError as error:
            raise FileUnusable.from_os(link, error) from None
        removed.append(link)
    if not keep_data and planned.state_exists and planned.state.resolve() != planned.root.resolve():
        try:
            shutil.rmtree(planned.state)
        except OSError as error:
            raise FileUnusable.from_os(planned.state, error) from None
        removed.append(planned.state)
    try:
        shutil.rmtree(planned.root)
    except OSError as error:
        raise FileUnusable.from_os(planned.root, error) from None
    removed.append(planned.root)
    return removed
