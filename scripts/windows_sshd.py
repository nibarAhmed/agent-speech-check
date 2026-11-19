"""Windows' OpenSSH server, so the real-ssh e2e tests run on Windows: asc's ssh reaches
localhost, as e2e.yml's Orca job does on Linux.

  download FOLDER   fetch the pinned zip into FOLDER and print its path (any machine)
  install ZIP       install and start sshd, and let this user ssh to localhost with a key

install runs only in Windows Sandbox or on a CI runner: it replaces any sshd service and
writes this user's ~/.ssh.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from agent_speech_check.windows.wait import poll_until

VERSION = "10.4.0.0p1-Preview"
URL = f"https://github.com/PowerShell/Win32-OpenSSH/releases/download/{VERSION}/OpenSSH-Win64.zip"
SHA256 = "b67ff8becd203502190ec42ac7787c24c2cd3325ed7700485fe7a1c83d2a7957"
ZIP = f"OpenSSH-Win64-{VERSION}.zip"
SERVER = Path(r"C:\OpenSSH-Win64")
ADMIN_KEYS = (
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    / "ssh"
    / "administrators_authorized_keys"
)


def download(folder: Path) -> Path:
    zip_path = folder / ZIP
    if not zip_path.exists():
        with urllib.request.urlopen(URL, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            sys.exit(f"{URL}: SHA-256 mismatch")
        zip_path.write_bytes(data)
    return zip_path


def throwaway() -> bool:
    return os.environ.get("USERNAME") == "WDAGUtilityAccount" or "GITHUB_ACTIONS" in os.environ


def powershell(script: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=True
    )


def keyscan() -> str:
    found = subprocess.run(
        [SERVER / "ssh-keyscan.exe", "localhost"], capture_output=True, text=True, check=False
    )
    return found.stdout


def install(zip_path: Path) -> None:
    if not throwaway():
        sys.exit("install only in Windows Sandbox or on a CI runner")
    shutil.unpack_archive(zip_path, SERVER.parent)
    powershell(f"& '{SERVER / 'install-sshd.ps1'}'")
    powershell("Start-Service sshd")
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(exist_ok=True)
    key = ssh_dir / "id_ed25519"
    if not key.exists():
        subprocess.run(
            [SERVER / "ssh-keygen.exe", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True
        )
    # sshd ignores this file unless only Administrators and SYSTEM can write it.
    shutil.copyfile(key.with_suffix(".pub"), ADMIN_KEYS)
    grant = ["/grant", "*S-1-5-32-544:F", "/grant", "*S-1-5-18:F"]
    subprocess.run(
        ["icacls", ADMIN_KEYS, "/inheritance:r", *grant], check=True, stdout=subprocess.DEVNULL
    )
    if not poll_until(lambda: bool(keyscan()), 30, 0.5):
        sys.exit("sshd doesn't answer on localhost")
    with (ssh_dir / "known_hosts").open("a", encoding="utf-8") as known:
        known.write(keyscan())
    # The ssh asc runs, from PATH.
    print("ssh:", shutil.which("ssh"))
    subprocess.run(["ssh", "-V"], check=True)
    subprocess.run(["ssh", "-o", "BatchMode=yes", "localhost", "exit"], check=True)
    print("ssh to localhost: ok")


def main() -> None:
    command, path = sys.argv[1:3]
    if command == "download":
        print(download(Path(path)))
    elif command == "install":
        install(Path(path))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
