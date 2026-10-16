"""Run `pytest -m e2e` against a real portable NVDA inside Windows Sandbox.

Never runs any NVDA binary on this machine: NVDA's command line acts on the
copy that is already running (your screen reader). Everything NVDA happens
inside the sandbox. This script only downloads the installer.

Prepares `.e2e/` (git-ignored):
  in/py/       uv-managed CPython (relocatable, unlike the Store Python)
  in/site/     asc plus test dependencies, installed for that Python
  in/nvda_<v>.exe
  out/         written by the sandbox: progress.txt, pytest.txt, DONE

Then starts the sandbox with `wsb`, the repo mapped read-only, runs the tests
in its user session with `wsb exec`, and waits for DONE.
The sandbox stops Windows Audio first (silent on the host), creates the
portable NVDA at C:\\nvda, runs the e2e tests, and shuts itself down.

The sandbox window is kept hidden behind an "asc e2e sandbox" tray icon, as in
sandbox mode (see `sandbox/wsb.py`), so it never takes focus or catches keys typed
on the host. The icon shows and hides it; pass --show to keep it visible.

Usage: uv run python scripts/e2e_sandbox.py [--show] [--nvda-version V] [pytest args...]

--nvda-version tests another NVDA than the pinned one, for example 2025.1,
the oldest with Remote Access (see docs/findings.md for how it fares).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_speech_check.nvda import launcher
from agent_speech_check.sandbox import wsb

PYTHON = "3.13"
REPO = Path(__file__).resolve().parents[1]
WORK = REPO / ".e2e"
IN = WORK / "in"
OUT = WORK / "out"
TIMEOUT_S = 20 * 60
SANDBOX_REPO = r"C:\asc\repo"
SANDBOX_IN = r"C:\asc\in"
SANDBOX_OUT = r"C:\asc\out"
SANDBOX_WORK = SANDBOX_REPO.replace("repo", "work")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def python_exe() -> Path | None:
    """The real install folder, not uv's minor-version junction, which points at
    a host path the sandbox can't see."""
    for exe in sorted((IN / "py").glob("cpython-*/python.exe")):
        if not exe.parent.is_junction():
            return exe
    return None


def prepare(nvda_version: str) -> None:
    IN.mkdir(parents=True, exist_ok=True)
    py_root = IN / "py"
    if python_exe() is None:
        run(
            "uv",
            "python",
            "install",
            PYTHON,
            "--install-dir",
            str(py_root),
            "--no-bin",
            "--no-registry",
        )
    python = python_exe()
    assert python is not None  # noqa: S101 - installed just above
    site = IN / "site"
    if site.exists():
        shutil.rmtree(site)
    run(
        "uv",
        "pip",
        "install",
        "--quiet",
        "--python",
        str(python),
        "--target",
        str(site),
        str(REPO),
        "pytest",
        "pytest-asyncio",
    )
    installer = IN / f"nvda_{nvda_version}.exe"
    if not installer.exists():
        shutil.copy2(launcher.download(nvda_version), installer)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()


def write_cmd(nvda_version: str, pytest_args: list[str]) -> Path:
    python = python_exe()
    assert python is not None  # noqa: S101 - prepare() ran first
    py = f"{SANDBOX_IN}\\py\\{python.parent.name}\\python.exe"
    args = " ".join(pytest_args)
    cmd = WORK / "run.cmd"
    cmd.write_text(
        "@echo off\r\n"
        f"set OUT={SANDBOX_OUT}\r\n"
        "echo started %TIME% > %OUT%\\progress.txt\r\n"
        "net stop audiosrv /y >> %OUT%\\progress.txt 2>&1\r\n"
        f"{SANDBOX_IN}\\nvda_{nvda_version}.exe --create-portable-silent --portable-path=C:\\nvda"
        " >> %OUT%\\progress.txt 2>&1\r\n"
        ":waitnvda\r\n"
        "if not exist C:\\nvda\\nvda.exe (\r\n"
        "  timeout /t 2 /nobreak > nul\r\n"
        "  goto waitnvda\r\n"
        ")\r\n"
        "timeout /t 5 /nobreak > nul\r\n"
        "echo portable ready %TIME% >> %OUT%\\progress.txt\r\n"
        "set ASC_E2E_NVDA=C:\\nvda\r\n"
        f"set ASC_E2E_INSTALLER={SANDBOX_IN}\\nvda_{nvda_version}.exe\r\n"
        "set ASC_E2E_OUT=%OUT%\r\n"
        f"set PYTHONPATH={SANDBOX_IN}\\site;{SANDBOX_REPO}\r\n"
        "set PYTHONDONTWRITEBYTECODE=1\r\n"
        f"cd /d {SANDBOX_REPO}\r\n"
        f"{py} -m pytest -m e2e tests\\e2e -p no:cacheprovider -v {args}"
        " > %OUT%\\pytest.txt 2>&1\r\n"
        "echo %ERRORLEVEL% > %OUT%\\DONE\r\n"
        "shutdown /s /t 5\r\n",
        encoding="utf-8",
    )
    return cmd


def mapped() -> list[wsb.Mapped]:
    return [
        wsb.Mapped(str(REPO), SANDBOX_REPO),
        wsb.Mapped(str(IN), SANDBOX_IN),
        wsb.Mapped(str(OUT), SANDBOX_OUT, read_only=False),
        wsb.Mapped(str(WORK), SANDBOX_WORK),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--nvda-version", default=launcher.VERSION)
    ours, pytest_args = parser.parse_known_args()
    if not re.fullmatch(launcher.VERSION_PATTERN, ours.nvda_version):
        parser.error(f"--nvda-version: not an NVDA version: {ours.nvda_version}")
    wsb.require_available()
    if wsb.running():
        print("a Windows Sandbox is already running; stop it first")
        return 3
    print(f"NVDA {ours.nvda_version}")
    prepare(ours.nvda_version)
    cmd = write_cmd(ours.nvda_version, pytest_args)
    tooltip = "asc e2e sandbox: show or hide"
    with wsb.SandboxWindow("show" if ours.show else "tray", tooltip) as window:
        return run_tests(window, cmd)


def run_tests(window: wsb.SandboxWindow, cmd: Path) -> int:
    config = wsb.config_xml(mapped(), networking=False)
    sandbox_id = wsb.start_connected(config, window, WORK / "guard.err")
    # `window.window` is "taskbar" if the tray icon could not be added.
    where = {"tray": ", in the tray", "taskbar": ", minimised"}.get(window.window, "")
    print(f"sandbox {sandbox_id} started{where}")
    runner = subprocess.Popen(
        [
            "wsb",
            "exec",
            "--id",
            sandbox_id,
            "--run-as",
            "ExistingLogin",
            "-c",
            f"cmd /c {SANDBOX_WORK}{chr(92)}{cmd.name}",
            "--raw",
        ],
        stdout=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + TIMEOUT_S
    done = OUT / "DONE"
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    runner.kill()
    report = OUT / "pytest.txt"
    print(report.read_text(encoding="utf-8", errors="replace") if report.exists() else "")
    if sandbox_id in wsb.running():
        wsb.stop(sandbox_id)
    wsb.wait_gone(120)
    if not done.exists():
        print(f"no result after {TIMEOUT_S // 60} min; see {OUT / 'progress.txt'}")
        return 1
    return int(done.read_text(encoding="utf-8").strip() or "1")


if __name__ == "__main__":
    sys.exit(main())
