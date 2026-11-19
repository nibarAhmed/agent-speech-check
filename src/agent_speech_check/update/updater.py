"""Keeping an installed `asc` up to date from this repo's GitHub releases.

After `asc stop`, at most once a day, `asc _update` runs in the background: it
asks GitHub for the latest release, downloads its MSI or Linux tarball, checks it
against the SHA-256 GitHub lists, and installs it once no `asc` runs: the MSI runs
quietly, and the tarball's version takes over the install's `current` link.
`auto_update = false` in any instance's config turns it off. Any other `asc` (uv,
a clone, a test) never updates itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agent_speech_check import __version__
from agent_speech_check.config.targets import load
from agent_speech_check.daemon import state
from agent_speech_check.errors import AscError
from agent_speech_check.windows import spawn, tls, winapi
from agent_speech_check.windows.paths import base_root, every_root, write_atomic
from agent_speech_check.windows.wait import poll_until

if sys.platform == "win32":
    import winreg

log = logging.getLogger(__name__)

REPO = "nibarAhmed/agent-speech-check"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
# `uname -m` of the Linux machines a tarball is built for.
LINUX_MACHINES = ("x86_64", "aarch64")
# The tarball's one top folder, which holds versions/V and the `current` link.
LINUX_TOP = "agent-speech-check"
# The entry the MSI writes; its InstallLocation is the folder it installed.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\agent-speech-check"
CHECK_EVERY_S = 24 * 60 * 60
RETRY_S = 60 * 60
LOOKUP_TIMEOUT_S = 15
LOOKUP_MAX_BYTES = 1_000_000
DOWNLOAD_TIMEOUT_S = 60
# A stopped daemon still runs for a moment after its state file goes.
IDLE_WAIT_S = 30
# The installer waits for every asc.exe to exit, so this process must not hang.
GIVE_UP_S = 15 * 60


@dataclass(frozen=True)
class Release:
    version: tuple[int, int, int]
    url: str
    sha256: str  # of the asset, lowercase hex

    @property
    def name(self) -> str:
        return ".".join(map(str, self.version))


def folder() -> Path:
    return base_root() / "update"


def stamp() -> Path:
    """Touched by each update check."""
    return folder() / "checked"


def marker() -> Path:
    """Names the msiexec that installs an update, while it runs."""
    return folder() / "installing.json"


def asset_name(version: str, system: str = sys.platform, machine: str | None = None) -> str | None:
    """The release asset that installs `version` here, if one is built for it."""
    if system == "win32":
        return f"agent-speech-check-{version}.msi"
    # Not a default: on Windows platform.machine() asks WMI, at every asc's import.
    machine = machine or platform.machine()
    if system == "linux" and machine in LINUX_MACHINES:
        return linux_asset(machine)
    return None


def linux_asset(machine: str) -> str:
    return f"agent-speech-check-linux-{machine}.tar.gz"


def _version(text: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def parse_release(
    raw: object, system: str = sys.platform, machine: str | None = None
) -> Release | None:
    """GitHub's latest release, if it has this platform's asset from this repo with a SHA-256."""
    if not isinstance(raw, dict):
        return None
    tag = raw.get("tag_name")
    version = _version(tag[1:]) if isinstance(tag, str) and tag.startswith("v") else None
    if version is None:
        return None
    name = asset_name(".".join(map(str, version)), system, machine)
    assets = raw.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        url, digest = asset.get("browser_download_url"), asset.get("digest")
        if not isinstance(url, str) or not url.startswith(DOWNLOAD_PREFIX):
            return None
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            return None
        return Release(version, url, digest.removeprefix("sha256:").lower())
    return None


def newer(release: Release, current: str) -> bool:
    """`release` is newer than the `current` version. An unknown version is not older."""
    have = _version(current)
    return have is not None and release.version > have


def due(stamp: Path, now: float) -> bool:
    """No check in the last day, by the time `stamp` was touched."""
    try:
        return not 0 <= now - stamp.stat().st_mtime < CHECK_EVERY_S
    except FileNotFoundError:
        return True


def defer(stamp: Path, now: float) -> None:
    """Makes the next check due in RETRY_S, not a day."""
    then = now - CHECK_EVERY_S + RETRY_S
    stamp.touch()
    os.utime(stamp, (then, then))


def enabled() -> bool:
    """On unless any instance's config says `auto_update = false`. A config that
    can't be read may say so too."""
    for root in every_root():
        try:
            if load(root / "config.toml").value("auto_update")[0] is False:
                return False
        except AscError:
            return False
    return True


def install_root(exe: Path) -> Path | None:
    """The folder the Linux tarball was unpacked into, if `exe` is ROOT/versions/V/asc
    and ROOT/current is the link that picks a version."""
    root = exe.parent.parent.parent
    if exe.parent.parent.name != "versions" or not (root / "current").is_symlink():
        return None
    return root


def installed_exe() -> Path | None:
    """This asc, if the MSI installed it, or it runs from an unpacked Linux tarball."""
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            return None
        exe = Path(sys.executable)
        same = os.path.normcase(exe.parent) == os.path.normcase(Path(str(location)))
        return exe if same else None
    exe = Path(sys.executable).resolve()
    return exe if install_root(exe) else None


def checking() -> Path:
    """Names the `asc _update` that `asc stop` started, so the installer can end it."""
    return folder() / "checking.json"


def mark(path: Path, pid: int) -> None:
    started = winapi.created_at(pid)
    if started is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"pid": pid, "started": started}))


def _marked(path: Path) -> int | None:
    """The pid `path` names, while that process runs."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # The start time tells a reused pid apart.
        return int(raw["pid"]) if winapi.created_at(raw["pid"]) == raw["started"] else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def installing() -> bool:
    """An update's msiexec runs: asc.exe and its files are being replaced."""
    return _marked(marker()) is not None


def stop_check() -> None:
    """For the installer: end an update check, which may otherwise run for minutes."""
    pid = _marked(checking())
    if pid is not None:
        winapi.terminate(pid, winapi.KILL_WAIT_S)


def _running() -> bool:
    return any(state.folders(root) for root in every_root())


def _images(exe: Path) -> list[Path]:
    """The exes of this install that an asc may run from: on Linux, every version's."""
    root = install_root(exe) if sys.platform != "win32" else None
    return [exe] if root is None else [path / "asc" for path in (root / "versions").iterdir()]


def start_if_due() -> None:
    """After `asc stop`: check for an update in the background, if one is due."""
    exe = installed_exe()
    if exe is None:
        return
    checked = stamp()
    # An update check never fails asc stop.
    with contextlib.suppress(OSError):
        if _running() or not due(checked, time.time()) or not enabled():
            return
        checked.parent.mkdir(parents=True, exist_ok=True)
        checked.touch()
        # Its log, and any crash, land in update.log.
        with (folder() / "update.log").open("wb") as err:
            mark(checking(), spawn.detached([str(exe), "_update"], err, folder()).pid)


def run() -> None:
    """`asc _update`: install the latest release if it is newer and asc is idle."""
    exe = installed_exe()
    if exe is None:
        return
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    watchdog = threading.Timer(GIVE_UP_S, os._exit, (1,))
    watchdog.daemon = True
    watchdog.start()
    try:
        release = latest()
        if release is None:
            log.warning("the latest release has nothing to trust for this platform")
            return
        if not newer(release, __version__):
            log.info("up to date: %s", __version__)
            return
        package = download(release)
        if package is None:
            log.warning("the download's SHA-256 is not the one GitHub lists")
            return
        images = _images(exe)
        idle = poll_until(
            lambda: not (_running() or winapi.others_running(*images)), IDLE_WAIT_S, 0.5
        )
        if not idle or not enabled():
            # The download is kept for the retry.
            defer(stamp(), time.time())
            log.info("not installing %s now: asc runs, or auto_update is off", release.name)
            return
        if sys.platform == "win32":
            install(package)
        else:
            unpack(package, exe, release.name)
        log.info("installing %s over %s", release.name, __version__)
    # URLError is an OSError. A reply that isn't HTTP raises HTTPException.
    except (OSError, http.client.HTTPException, tarfile.TarError, AscError) as error:
        log.warning("update check failed: %s", getattr(error, "reason", error))


def latest() -> Release | None:
    request = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json"})
    with tls.urlopen(request, LOOKUP_TIMEOUT_S) as response:
        text = response.read(LOOKUP_MAX_BYTES)
    try:
        return parse_release(json.loads(text))
    except ValueError:
        return None


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def download(release: Release) -> Path | None:
    """The release's asset, if it matches its SHA-256. Kept until a newer one."""
    suffix = ".msi" if sys.platform == "win32" else ".tar.gz"
    package = folder() / f"agent-speech-check-{release.name}{suffix}"
    package.parent.mkdir(parents=True, exist_ok=True)
    if package.exists() and _sha256(package) == release.sha256:
        return package
    for old in folder().glob("agent-speech-check-*"):
        old.unlink(missing_ok=True)
    partial = package.with_suffix(".part")
    try:
        with (
            tls.urlopen(release.url, DOWNLOAD_TIMEOUT_S) as response,
            partial.open("wb") as file,
        ):
            shutil.copyfileobj(response, file)
        if _sha256(partial) != release.sha256:
            return None
        partial.replace(package)
    finally:
        partial.unlink(missing_ok=True)
    return package


def install(msi: Path) -> None:
    """Start msiexec and return at once: it replaces this asc.exe, so it waits for
    every asc.exe to exit (the MSI's StopDaemons step)."""
    msiexec = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "msiexec.exe"
    log_file = folder() / "msiexec.log"
    command = [str(msiexec), "/i", str(msi), "/qn", "/norestart", "/l*", str(log_file)]
    mark(marker(), spawn.detached(command, subprocess.DEVNULL).pid)


def unpack(tarball: Path, exe: Path, version: str) -> None:
    """Point the Linux install's `current` link at `version` from `tarball`, in one rename,
    so a crash leaves the old version or the new, never neither. Then remove the
    versions nothing runs: all but the new one and this process's own."""
    root = install_root(exe)
    if root is None:
        raise FileNotFoundError(exe)
    new = root / "versions" / version
    if (root / "current").resolve() == new:
        return
    staging = root / ".unpacking"
    shutil.rmtree(staging, ignore_errors=True)
    with tarfile.open(tarball) as tar:
        tar.extractall(staging, filter="data")
    shutil.rmtree(new, ignore_errors=True)
    (staging / LINUX_TOP / "versions" / version).rename(new)
    shutil.rmtree(staging)
    link = root / ".current"
    link.unlink(missing_ok=True)
    link.symlink_to(Path("versions") / version)
    link.replace(root / "current")
    for old in (root / "versions").iterdir():
        if old not in (new, exe.parent):
            shutil.rmtree(old, ignore_errors=True)
