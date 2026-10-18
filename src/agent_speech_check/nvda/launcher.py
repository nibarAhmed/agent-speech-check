"""The portable NVDA that desktop mode launches.

Starting any NVDA binary while another NVDA runs restarts the running one (the
NVDA user guide says so), and that one may be the user's screen reader. So
creating the portable copy and launching it both refuse while any `nvda.exe`
runs, and `asc` stops its NVDA by its own process handle, never with `nvda -q`.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check.errors import (
    DownloadFailed,
    InstallerInUse,
    InstallerTimedOut,
    NvdaAlreadyRunning,
    NvdaNotFound,
    NvdaTooOld,
    PortableFailed,
    PortableNotReplaced,
    ProgramWontStart,
)
from agent_speech_check.nvda import nvda_config
from agent_speech_check.nvda.nvda_config import RelayAddress, Setting
from agent_speech_check.windows import winapi
from agent_speech_check.windows.paths import state_root
from agent_speech_check.windows.wait import poll_until

log = logging.getLogger(__name__)

# NV Access's update check, the one NVDA itself asks: the latest stable NVDA.
UPDATE_URL = "https://api.nvaccess.org/nvdaUpdateCheck?versionType=stable"
# What an NVDA release version looks like: 2026.2, 2025.1.1.
VERSION_PATTERN = r"\d{4}\.\d+(\.\d+)?"
# Remote Access is built in from 2025.1, but 2025.1 forwards no speech without an
# audio device (docs/findings.md). 2026.2 is the version tested; those between are not.
MIN_VERSION = (2026, 2)
# Per blocking read, not for the whole file: a stalled connection fails.
DOWNLOAD_TIMEOUT_S = 60
# The lookup runs before desktop and sandbox starts, so offline it gives up soon.
LOOKUP_TIMEOUT_S = 5
# The update check answers a few short lines.
LOOKUP_MAX_BYTES = 4096
CREATE_TIMEOUT_S = 180
STOP_TIMEOUT_S = 10


def managed_dir() -> Path:
    """Where `asc get-nvda` puts the portable copy."""
    return state_root() / "nvda" / "portable"


def folder_for(nvda_dir: str | None) -> Path:
    """A mode's portable NVDA: its `nvda_dir`, else the one `asc get-nvda` made."""
    return Path(nvda_dir) if nvda_dir else managed_dir()


def installers_dir() -> Path:
    """Where `asc get-nvda` and sandbox mode keep the downloaded installer."""
    return state_root() / "nvda"


def installer_path(version: str) -> Path:
    return installers_dir() / f"nvda_{version}.exe"


def cached_installer() -> Path | None:
    """The installer the last download left, or None. `download` keeps only one,
    but if there are more, the highest version."""
    found = {}
    for path in installers_dir().glob("nvda_*.exe"):
        version = path.stem.removeprefix("nvda_")
        if re.fullmatch(VERSION_PATTERN, version):
            found[version_numbers(version)] = path
    return found[max(found)] if found else None


def version_numbers(version: str) -> tuple[int, ...]:
    """A version like 2026.2.1 as numbers, to compare."""
    return tuple(int(n) for n in version.split("."))


def installer_url(version: str) -> str:
    return f"https://download.nvaccess.org/releases/{version}/nvda_{version}.exe"


@dataclass(frozen=True)
class Release:
    """The latest stable NVDA, as NV Access's update check names it."""

    version: str
    sha1: str  # of the installer, lowercase hex


def parse_release(text: str) -> Release | None:
    """The update check's `key: value` lines, or None if they name no valid release."""
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    version, sha1 = fields.get("version", ""), fields.get("launcherHash", "").lower()
    # The version goes into the download URL and a file name.
    if re.fullmatch(VERSION_PATTERN, version) and re.fullmatch(r"[0-9a-f]{40}", sha1):
        return Release(version, sha1)
    return None


@contextmanager
def _failing_as_download(url: str) -> Iterator[None]:
    """Turn what a failed fetch of `url` raises into DownloadFailed."""
    try:
        yield
    # URLError and HTTPError are OSErrors. A reply that isn't HTTP (a captive
    # portal, a broken proxy) raises HTTPException, which is not.
    except (OSError, http.client.HTTPException) as error:
        raise DownloadFailed(
            url, str(getattr(error, "reason", error)) or type(error).__name__
        ) from None


def _read_update_check(into: list[str | DownloadFailed]) -> None:
    try:
        with (
            _failing_as_download(UPDATE_URL),
            urllib.request.urlopen(UPDATE_URL, timeout=LOOKUP_TIMEOUT_S) as response,
        ):
            into.append(response.read(LOOKUP_MAX_BYTES).decode("utf-8", "replace"))
    except DownloadFailed as error:
        into.append(error)


def latest() -> Release:
    """Ask NV Access for the latest stable NVDA, within LOOKUP_TIMEOUT_S in all."""
    # urlopen's timeout covers each socket wait, not name resolution or a slow
    # reply. A daemon thread can be left behind: it never holds asc's exit.
    answer: list[str | DownloadFailed] = []
    reader = threading.Thread(target=_read_update_check, args=(answer,), daemon=True)
    reader.start()
    reader.join(LOOKUP_TIMEOUT_S)
    if not answer:
        raise DownloadFailed(UPDATE_URL, f"no answer within {LOOKUP_TIMEOUT_S:g} s")
    if isinstance(answer[0], DownloadFailed):
        raise answer[0]
    release = parse_release(answer[0])
    if release is None:
        raise DownloadFailed(UPDATE_URL, "the answer names no NVDA version")
    return release


def newer(release: Release, have: tuple[int, ...] | None) -> bool:
    """`release` is newer than the version `have`. An unknown version is not older."""
    return have is not None and version_numbers(release.version) > have


def update_for(exe: Path) -> Release | None:
    """The latest NVDA if it is newer than `exe`, else None. None too when NV
    Access can't be reached: a start never fails on this check."""
    have = winapi.file_version(exe)
    if have is None:
        return None
    try:
        release = latest()
    except DownloadFailed:
        return None
    return release if newer(release, have) else None


def latest_installer(note: Callable[[str], object] = log.warning) -> Path:
    """The latest NVDA's installer, downloaded once. When NV Access can't be
    reached, the installer an earlier download left, said through `note`."""
    try:
        release = latest()
        return download(release.version, release.sha1)
    except DownloadFailed:
        cached = cached_installer()
        if cached is None:
            raise
    # Not checked by a lookup, so it could be one --version pinned below the minimum.
    hint = "connect to the network, or asc get-nvda"
    check_version(cached, winapi.file_version(cached), hint)
    note(f"cannot get the latest NVDA, so using {cached.name}")
    return cached


def sha1_matches(path: Path, sha1: str) -> bool:
    with path.open("rb") as file:
        # NV Access lists SHA-1. It shows the download is whole.
        return hashlib.file_digest(file, "sha1").hexdigest() == sha1


def running() -> bool:
    return bool(winapi.process_ids("nvda.exe"))


def refuse_if_running() -> None:
    if running():
        raise NvdaAlreadyRunning


def portable_hint(folder: Path) -> str:
    """What to do when the portable NVDA in `folder` is missing or too old."""
    # asc get-nvda can't help when you named the folder: nvda_dir overrides it.
    return "asc get-nvda" if folder == managed_dir() else "check nvda_dir"


def check_version(folder: Path, version: tuple[int, ...] | None, hint: str | None = None) -> None:
    """Refuse an NVDA older than MIN_VERSION. An unknown version is let through.
    `hint` defaults to the one for a portable NVDA in `folder`."""
    if version is not None and version < MIN_VERSION:
        found = ".".join(map(str, version[:2]))
        needed = ".".join(map(str, MIN_VERSION))
        raise NvdaTooOld(str(folder), found, needed, hint or portable_hint(folder))


def require_exe(folder: Path) -> Path:
    exe = folder / "nvda.exe"
    if not exe.is_file():
        raise NvdaNotFound([f"the portable NVDA ({folder})"], hint=portable_hint(folder))
    check_version(folder, winapi.file_version(exe))
    return exe


def download(version: str, sha1: str | None = None) -> Path:
    """Download the installer once, and check it against `sha1` if given. Only a
    file download: nothing runs."""
    installer = installer_path(version)
    installer.parent.mkdir(parents=True, exist_ok=True)
    # A kept installer can be damaged, or not ours: an e2e test puts one there.
    if installer.exists() and sha1 is not None and not sha1_matches(installer, sha1):
        try:
            installer.unlink()
        # Held open, as by a running sandbox. Not DownloadFailed: the offline
        # fallback would then take this same file.
        except OSError as error:
            raise InstallerInUse(str(installer), error.strerror or str(error)) from None
    if not installer.exists():
        partial = installer.with_suffix(".part")
        url = installer_url(version)
        try:
            with (
                _failing_as_download(url),
                urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response,  # noqa: S310 - fixed https URL
                partial.open("wb") as file,
            ):
                shutil.copyfileobj(response, file)
        except DownloadFailed:
            partial.unlink(missing_ok=True)
            raise
        if sha1 is not None and not sha1_matches(partial, sha1):
            partial.unlink()
            raise DownloadFailed(url, "its SHA-1 is not the one NV Access lists")
        partial.replace(installer)
    # Also when it was kept: sandbox mode takes the highest version it finds.
    drop_other_installers(installer)
    return installer


def drop_other_installers(installer: Path) -> None:
    """Delete the installers of other NVDA versions next to `installer`."""
    for other in installer.parent.glob("nvda_*.exe"):
        if other != installer:
            other.unlink(missing_ok=True)


def create_portable(installer: Path, dest: Path) -> Path:
    """Run the NVDA installer to make a portable copy at `dest`."""
    refuse_if_running()
    return run_installer(installer, dest)


def run_installer(installer: Path, dest: Path, *, timeout_s: float = CREATE_TIMEOUT_S) -> Path:
    """`create_portable` without the check that no NVDA runs. One deadline
    covers the installer and the wait for its copy. The new copy replaces
    `dest` only once it is whole, so a failed update leaves the old one."""
    new, old = dest.with_name(f"{dest.name}.new"), dest.with_name(f"{dest.name}.old")
    if old.exists() and not dest.exists():
        old.rename(dest)  # an earlier swap stopped between its renames
    for stale in (new, old):
        try:
            if stale.exists():
                shutil.rmtree(stale)
        except OSError as error:  # a file in it is open
            raise PortableNotReplaced(str(dest), error.strerror or str(error)) from None
    deadline = time.monotonic() + timeout_s
    process = _start([str(installer), "--create-portable-silent", f"--portable-path={new}"])
    try:
        process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        # Its children too, or they would go on writing into `dest`.
        left = winapi.kill_tree(process.pid)
        if process.pid not in left:
            process.wait()  # it is gone, so this returns at once and records its exit
        raise InstallerTimedOut(timeout_s, left) from None
    if not poll_until((new / "nvda.exe").is_file, max(0.0, deadline - time.monotonic()), 1):
        raise PortableFailed(str(new))
    # Renames, so a crash between them leaves `old`, which the next run puts back.
    try:
        if dest.exists():
            dest.rename(old)
        new.rename(dest)
    except OSError as error:  # a file in it is open
        if old.exists() and not dest.exists():
            old.rename(dest)
        raise PortableNotReplaced(str(dest), error.strerror or str(error)) from None
    shutil.rmtree(old, ignore_errors=True)
    return dest / "nvda.exe"


def _start(args: list[str]) -> subprocess.Popen[bytes]:
    """Start NVDA or its installer. A file Windows won't run is a setup problem, not a bug."""
    try:
        return subprocess.Popen(args)  # noqa: S603 - NVDA, or the NVDA installer
    except OSError as error:
        raise ProgramWontStart(args[0], error.strerror or str(error)) from None


@dataclass
class Running:
    process: subprocess.Popen[bytes]
    job: int

    def stop(self) -> None:
        """Kill NVDA and everything it started, by closing its job."""
        winapi.close_handle(self.job)
        try:
            self.process.wait(STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self.process.kill()


def launch(
    folder: Path,
    config_dir: Path,
    relay: RelayAddress,
    settings: tuple[Setting, ...],
) -> Running:
    """Start the portable NVDA with a copy of its own config that connects to the relay."""
    exe = require_exe(folder)
    refuse_if_running()
    nvda_config.write(config_dir, relay, settings, nvda_config.source_config(folder))
    process = _start(
        [
            str(exe),
            f"--config-path={config_dir}",
            f"--log-file={config_dir.parent / 'nvda.log'}",
            "-l",
            "20",
        ]
    )
    try:
        job = winapi.kill_on_close_job(process.pid)
    except OSError:
        process.kill()
        raise
    log.info("started NVDA, pid %d", process.pid)
    return Running(process, job)
