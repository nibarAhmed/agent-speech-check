"""Run the real-Orca and real-ssh tests in a throwaway Linux container, as e2e.yml's Orca
job does, from Windows or Linux with Docker.

The container is that job's: its image, its install and ssh steps and its test command,
read from e2e.yml. Only asc differs: this checkout's (its tracked and untracked files,
not the ignored ones), not the job's release build. The container goes when the run
ends. The image `asc-e2e-linux` stays as a cache: `docker rmi asc-e2e-linux` removes it.

Usage: uv run python scripts/e2e_linux.py [pytest args...]
For example `-k watch` runs only the tests named watch.
"""

from __future__ import annotations

import io
import os
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "e2e.yml"
IMAGE = "asc-e2e-linux"
UV = "0.13.0"
LOCKED = ("pyproject.toml", "uv.lock", ".python-version")


def image() -> str:
    found = re.search(r"^\s+image: (ubuntu:\S+)$", WORKFLOW.read_text(encoding="utf-8"), re.M)
    if found is None:
        sys.exit(f"{WORKFLOW}: no container image")
    return found.group(1)


def step(name: str) -> str:
    """The `run` of the Orca job's step `name`: a `|` block as it is, a `>-` one as one line."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"), None)
    if start is None:
        sys.exit(f"{WORKFLOW}: no step {name!r}")
    for i in range(start + 1, len(lines)):
        key = lines[i].strip()
        if key.startswith("- "):
            break
        if key in ("run: |", "run: >-"):
            indent = " " * (len(lines[i]) - len(lines[i].lstrip()) + 2)
            block = []
            for line in lines[i + 1 :]:
                if line.strip() and not line.startswith(indent):
                    break
                block.append(line.removeprefix(indent))
            if key == "run: |":
                return "\n".join(block).strip() + "\n"
            return " ".join(line.strip() for line in block).strip()
    sys.exit(f"{WORKFLOW}: step {name!r} has no run")


def tar(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def build() -> None:
    dockerfile = (
        f"FROM {image()}\n"
        "COPY install.sh /tmp/\n"
        "RUN DEBIAN_FRONTEND=noninteractive bash -e /tmp/install.sh\n"
        f"COPY --from=ghcr.io/astral-sh/uv:{UV} /uv /usr/local/bin/\n"
        "WORKDIR /src\n"
        f"COPY {' '.join(LOCKED)} ./\n"
        # Python and the dependencies, kept in the image until uv.lock changes.
        "RUN uv sync --locked --no-install-project\n"
    )
    context = {
        "Dockerfile": dockerfile.encode(),
        "install.sh": step("Install Orca and what asc runs with it").encode(),
        **{name: (REPO / name).read_bytes() for name in LOCKED},
    }
    subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-"], input=tar(context), check=True)


def checkout() -> bytes:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode()
    names = [name for name in listed.split("\0") if name and (REPO / name).is_file()]
    return tar({name: (REPO / name).read_bytes() for name in names})


def main() -> int:
    build()
    script = (
        # First: ssh reads stdin too.
        "tar -xf - -C /src\n"
        + step("Let root ssh to localhost with a key")
        + "uv sync --locked -q\n"
        + f'exec {step("Orca and ssh tests")} "$@"\n'
    )
    name = f"{IMAGE}-{os.getpid()}"
    run = ["docker", "run", "--rm", "-i", "--init", "--name", name]
    env = ["-e", "ASC_E2E_ORCA=1", "-e", "ASC_E2E_SSH=root@localhost"]
    print("$", shlex.join(["pytest", *sys.argv[1:]]), "in", image(), flush=True)
    try:
        return subprocess.run(
            [*run, *env, IMAGE, "bash", "-ec", script, "bash", *sys.argv[1:]],
            input=checkout(),
            check=False,
        ).returncode
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


if __name__ == "__main__":
    sys.exit(main())
