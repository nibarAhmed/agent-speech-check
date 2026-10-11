"""Run `pytest -m e2e` against a real portable NVDA inside Windows Sandbox.

Never runs any NVDA binary on this machine: NVDA's command line acts on the
copy that is already running (your screen reader). Everything NVDA happens
inside the sandbox. This script only downloads the pinned installer.

Prepares `.e2e/` (git-ignored):
  in/py/       uv-managed CPython (relocatable, unlike the Store Python)
  in/site/     asc plus test dependencies, installed for that Python
  in/nvda_<v>.exe
  out/         written by the sandbox: progress.txt, pytest.txt, DONE

Then starts the sandbox with `wsb`, the repo mapped read-only, runs the tests
in its user session with `wsb exec`, and waits for DONE.
The sandbox stops Windows Audio first (silent on the host), creates the
portable NVDA at C:\\nvda, runs the e2e tests, and shuts itself down.

The sandbox window is kept minimised (see `sandbox.py`), so it never takes
focus or catches keys typed on the host; pass --show to keep it visible.

Usage: uv run python scripts/e2e_sandbox.py [--show] [pytest args...]
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_speech_check import nvda, sandbox

NVDA_VERSION = nvda.VERSION
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


def prepare() -> None:
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
    installer = IN / f"nvda_{NVDA_VERSION}.exe"
    if not installer.exists():
        shutil.copy2(nvda.download(NVDA_VERSION), installer)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()


def write_cmd(pytest_args: list[str]) -> Path:
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
        f"{SANDBOX_IN}\\nvda_{NVDA_VERSION}.exe --create-portable-silent --portable-path=C:\\nvda"
        " >> %OUT%\\progress.txt 2>&1\r\n"
        ":waitnvda\r\n"
        "if not exist C:\\nvda\\nvda.exe (\r\n"
        "  timeout /t 2 /nobreak > nul\r\n"
        "  goto waitnvda\r\n"
        ")\r\n"
        "timeout /t 5 /nobreak > nul\r\n"
        "echo portable ready %TIME% >> %OUT%\\progress.txt\r\n"
        "set ASC_E2E_NVDA=C:\\nvda\r\n"
        f"set ASC_E2E_INSTALLER={SANDBOX_IN}\\nvda_{NVDA_VERSION}.exe\r\n"
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


def mapped() -> list[sandbox.Mapped]:
    return [
        sandbox.Mapped(str(REPO), SANDBOX_REPO),
        sandbox.Mapped(str(IN), SANDBOX_IN),
        sandbox.Mapped(str(OUT), SANDBOX_OUT, read_only=False),
        sandbox.Mapped(str(WORK), SANDBOX_WORK),
    ]


def main() -> int:
    args = sys.argv[1:]
    show = "--show" in args
    pytest_args = [a for a in args if a != "--show"]
    sandbox.require_available()
    if sandbox.running():
        print("a Windows Sandbox is already running; stop it first")
        return 3
    prepare()
    cmd = write_cmd(pytest_args)
    with sandbox.SandboxWindow("show" if show else "taskbar") as window:
        return run_tests(window, cmd, show=show)


def run_tests(window: sandbox.SandboxWindow, cmd: Path, *, show: bool) -> int:
    config = sandbox.config_xml(mapped(), networking=False)
    sandbox_id = sandbox.start_connected(config, window, WORK / "guard.err")
    print(f"sandbox {sandbox_id} started{'' if show else ', minimised'}")
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
    if sandbox_id in sandbox.running():
        sandbox.stop(sandbox_id)
    sandbox.wait_gone(120)
    if not done.exists():
        print(f"no result after {TIMEOUT_S // 60} min; see {OUT / 'progress.txt'}")
        return 1
    return int(done.read_text(encoding="utf-8").strip() or "1")


if __name__ == "__main__":
    sys.exit(main())
