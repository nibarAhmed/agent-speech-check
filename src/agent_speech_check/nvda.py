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

from agent_speech_check import winapi
from agent_speech_check.errors import NvdaAlreadyRunning, NvdaNotFound, PortableFailed
from agent_speech_check.nvda_config import Setting, render
from agent_speech_check.paths import state_root

log = logging.getLogger(__name__)

VERSION = "2026.2"
CREATE_TIMEOUT_S = 180
STOP_TIMEOUT_S = 10


def managed_dir() -> Path:
    """Where `asc get-nvda` puts the portable copy."""
    return state_root() / "nvda" / "portable"


def folder_for(nvda_dir: str | None) -> Path:
    """A target's portable NVDA: its `nvda_dir`, else the one `asc get-nvda` made."""
    return Path(nvda_dir) if nvda_dir else managed_dir()


def installer_url(version: str) -> str:
    return f"https://download.nvaccess.org/releases/{version}/nvda_{version}.exe"


def refuse_if_running() -> None:
    if winapi.process_ids("nvda.exe"):
        raise NvdaAlreadyRunning


def require_exe(folder: Path) -> Path:
    exe = folder / "nvda.exe"
    if not exe.is_file():
        raise NvdaNotFound([f"the portable NVDA ({folder})"], hint="asc get-nvda")
    return exe


def download(version: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    installer = dest / f"nvda_{version}.exe"
    if not installer.exists():
        partial = installer.with_suffix(".part")
        urllib.request.urlretrieve(installer_url(version), partial)  # noqa: S310 - fixed https URL
        partial.replace(installer)
    return installer


def create_portable(installer: Path, dest: Path) -> Path:
    """Run the NVDA installer to make a portable copy at `dest`."""
    refuse_if_running()
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(  # noqa: S603 - the official NVDA installer
        [str(installer), "--create-portable-silent", f"--portable-path={dest}"],
        check=False,
        timeout=CREATE_TIMEOUT_S,
    )
    deadline = time.monotonic() + CREATE_TIMEOUT_S
    while not (dest / "nvda.exe").is_file():
        if time.monotonic() > deadline:
            raise PortableFailed(str(dest))
        time.sleep(1)
    return dest / "nvda.exe"


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
    relay: tuple[str, int, str],
    settings: tuple[Setting, ...],
) -> Running:
    """Start the portable NVDA with a fresh config that connects to the relay.

    `relay` is (channel, port, fingerprint).
    """
    exe = require_exe(folder)
    refuse_if_running()
    if config_dir.exists():
        shutil.rmtree(config_dir)
    config_dir.mkdir(parents=True)
    channel, port, fingerprint = relay
    ini = render(channel, port, fingerprint, settings)
    (config_dir / "nvda.ini").write_text(ini, encoding="utf-8")
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
