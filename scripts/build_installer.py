"""Build the asc MSI: PyInstaller (one folder), then WiX.

Run `uv run python scripts/build_installer.py`. Needs the .NET SDK for WiX, which
`dotnet-tools.json` pins. WiX 7 asks to accept its EULA (the Open Source Maintenance
Fee, owed only by organizations with over $10,000 a year in revenue); the build accepts
it for this project. Prints the path of dist/agent-speech-check-VERSION.msi.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"

# MSI versions are three numbers: at most 255, 255 and 65535.
MSI_LIMITS = (255, 255, 65535)


def wix_ui() -> str:
    """The wizard's dialogs, at the WiX version dotnet-tools.json pins."""
    tools = json.loads((ROOT / "dotnet-tools.json").read_text(encoding="utf-8"))
    return f"WixToolset.UI.wixext/{tools['tools']['wix']['version']}"


def require_x64(platform: str) -> None:
    """PyInstaller freezes the running Python, and the MSI is x64."""
    if platform != "win-amd64":
        sys.exit(f"The MSI needs 64-bit x86 Python, this one is {platform}")


def msi_version(version: str) -> str:
    """The pyproject version, if Windows Installer can use it as is."""
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", version)
    if match is None or any(
        int(part) > limit for part, limit in zip(match.groups(), MSI_LIMITS, strict=True)
    ):
        sys.exit(f"An MSI needs a version like 1.2.3, pyproject.toml has {version!r}")
    return version


def main() -> None:
    require_x64(sysconfig.get_platform())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = msi_version(pyproject["project"]["version"])
    subprocess.run(
        [
            "uv", "run", "--group", "build", "pyinstaller", "--noconfirm", "--onedir",
            "--name", "asc", "--copy-metadata", "agent-speech-check",
            "--paths", str(ROOT / "src"),
            "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD),
            str(ROOT / "src" / "agent_speech_check" / "__main__.py"),
        ],
        check=True,
        cwd=ROOT,
    )  # fmt: skip
    subprocess.run(["dotnet", "tool", "restore"], check=True, cwd=ROOT)
    ui = wix_ui()
    subprocess.run(
        ["dotnet", "wix", "extension", "add", "-acceptEula", "wix7", ui], check=True, cwd=ROOT
    )
    msi = DIST / f"agent-speech-check-{version}.msi"
    subprocess.run(
        [
            "dotnet", "wix", "build", str(ROOT / "installer" / "asc.wxs"),
            "-d", f"Version={version}", "-d", f"AppDir={DIST / 'asc'}",
            "-ext", ui, "-loc", str(ROOT / "installer" / "asc.wxl"),
            "-arch", "x64", "-wx", "-acceptEula", "wix7", "-o", str(msi),
        ],
        check=True,
        cwd=ROOT,
    )  # fmt: skip
    print(msi)


if __name__ == "__main__":
    main()
