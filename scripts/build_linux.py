"""Build the Linux release: PyInstaller (one folder), in a tarball.

Run `uv run python -m scripts.build_linux` on 64-bit x86 Linux. The app needs the glibc
it was built with or a newer one, so the release builds on an old one (manylinux_2_28).
Prints the path of dist/agent-speech-check-linux-x86_64.tar.gz.
"""

from __future__ import annotations

import sys
import sysconfig
import tarfile
from pathlib import Path

from scripts.build_installer import DIST, freeze, project

from agent_speech_check.update.updater import LINUX_ASSET, LINUX_TOP


def require_linux_x64(platform: str) -> None:
    """PyInstaller freezes the running Python, and the release is for x86_64."""
    if platform != "linux-x86_64":
        sys.exit(f"The Linux release needs 64-bit x86 Linux Python, this one is {platform}")


def _owned_by_root(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def write_tarball(app: Path, version: str, out: Path) -> None:
    """The layout the updater keeps: versions/VERSION holds the app, `current` links to it."""
    link = tarfile.TarInfo(f"{LINUX_TOP}/current")
    link.type = tarfile.SYMTYPE
    link.linkname = f"versions/{version}"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(app, f"{LINUX_TOP}/versions/{version}", filter=_owned_by_root)
        tar.addfile(_owned_by_root(link))


def main() -> None:
    require_linux_x64(sysconfig.get_platform())
    out = DIST / LINUX_ASSET
    write_tarball(freeze(), project()["version"], out)
    print(out)


if __name__ == "__main__":
    main()
