"""The portable NVDA that desktop mode launches.

Starting any NVDA binary while another NVDA runs restarts the running one (the
NVDA user guide says so), and that one may be the user's screen reader. So
creating the portable copy and launching it both refuse while any `nvda.exe`
runs, and `asc` stops its NVDA by its own process handle, never with `nvda -q`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check.errors import (
    DownloadFailed,
    InstallerTimedOut,
    NvdaAlreadyRunning,
    NvdaNotFound,
    NvdaTooOld,
    PortableFailed,
)
from agent_speech_check.nvda import nvda_config
from agent_speech_check.nvda.nvda_config import RelayAddress, Setting
from agent_speech_check.windows import winapi
from agent_speech_check.windows.paths import state_root
from agent_speech_check.windows.wait import poll_until

log = logging.getLogger(__name__)

VERSION = "2026.2"
# What an NVDA release version looks like: 2026.2, 2025.1.1.
VERSION_PATTERN = r"\d{4}\.\d+(\.\d+)?"
# Remote Access is built in from 2025.1, but 2025.1 forwards no speech without an
# audio device (docs/findings.md). 2026.2 is the version tested; those between are not.
MIN_VERSION = (2026, 2)
# Per blocking read, not for the whole file: a stalled connection fails.
DOWNLOAD_TIMEOUT_S = 60
CREATE_TIMEOUT_S = 180
STOP_TIMEOUT_S = 10


def managed_dir() -> Path:
    """Where `asc get-nvda` puts the portable copy."""
    return state_root() / "nvda" / "portable"


def folder_for(nvda_dir: str | None) -> Path:
    """A mode's portable NVDA: its `nvda_dir`, else the one `asc get-nvda` made."""
    return Path(nvda_dir) if nvda_dir else managed_dir()


def installer_path(version: str) -> Path:
    """Where `asc get-nvda` and sandbox mode keep the downloaded installer."""
    return state_root() / "nvda" / f"nvda_{version}.exe"


def installer_url(version: str) -> str:
    return f"https://download.nvaccess.org/releases/{version}/nvda_{version}.exe"


def running() -> bool:
    return bool(winapi.process_ids("nvda.exe"))


def refuse_if_running() -> None:
    if running():
        raise NvdaAlreadyRunning


def portable_hint(folder: Path) -> str:
    """What to do when the portable NVDA in `folder` is missing or too old."""
    # asc get-nvda can't help when you named the folder: nvda_dir overrides it.
    return "asc get-nvda" if folder == managed_dir() else "check nvda_dir"


def check_version(folder: Path, version: tuple[int, int] | None) -> None:
    """Refuse an NVDA older than MIN_VERSION. An unknown version is let through."""
    if version is not None and version < MIN_VERSION:
        found, needed = (".".join(map(str, v)) for v in (version, MIN_VERSION))
        raise NvdaTooOld(str(folder), found, needed, portable_hint(folder))


def require_exe(folder: Path) -> Path:
    exe = folder / "nvda.exe"
    if not exe.is_file():
        raise NvdaNotFound([f"the portable NVDA ({folder})"], hint=portable_hint(folder))
    check_version(folder, winapi.file_version(exe))
    return exe


def download(version: str) -> Path:
    """Download the installer once. Only a file download: nothing runs."""
    installer = installer_path(version)
    installer.parent.mkdir(parents=True, exist_ok=True)
    if not installer.exists():
        partial = installer.with_suffix(".part")
        url = installer_url(version)
        try:
            with (
                urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response,  # noqa: S310 - fixed https URL
                partial.open("wb") as file,
            ):
                shutil.copyfileobj(response, file)
        except OSError as error:  # URLError and HTTPError are OSErrors
            partial.unlink(missing_ok=True)
            raise DownloadFailed(url, str(getattr(error, "reason", error))) from None
        partial.replace(installer)
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
    covers the installer and the wait for its copy."""
    if dest.exists():
        shutil.rmtree(dest)
    deadline = time.monotonic() + timeout_s
    process = subprocess.Popen(  # noqa: S603 - the official NVDA installer
        [str(installer), "--create-portable-silent", f"--portable-path={dest}"]
    )
    try:
        process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        # Its children too, or they would go on writing into `dest`.
        left = winapi.kill_tree(process.pid)
        if process.pid not in left:
            process.wait()  # it is gone, so this returns at once and records its exit
        raise InstallerTimedOut(timeout_s, left) from None
    exe = dest / "nvda.exe"
    if not poll_until(exe.is_file, max(0.0, deadline - time.monotonic()), 1):
        raise PortableFailed(str(dest))
    return exe


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
    process = subprocess.Popen(  # noqa: S603 - the portable NVDA asc manages
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
