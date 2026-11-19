"""Processes, through psutil, on Windows and Linux."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import psutil


def process_ids(image: str) -> list[int]:
    """Pids of running processes whose exe file name is `image` (case-insensitive)."""
    image = image.lower()
    return [
        process.pid
        for process in psutil.process_iter(["name"])
        if (process.info["name"] or "").lower() == image
    ]


def image_path(pid: int) -> Path | None:
    """The full exe path of a process. None if it's gone or won't say."""
    try:
        exe = psutil.Process(pid).exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return Path(exe) if exe else None


def others_running(*images: Path) -> list[int]:
    """The pids of other processes running an exe at one of `images`. Ones that
    won't say their exe are left out."""
    wanted = {os.path.normcase(image) for image in images}
    return [
        process.pid
        for process in psutil.process_iter(["exe"])
        if process.pid != os.getpid()
        and process.info["exe"]
        and os.path.normcase(process.info["exe"]) in wanted
    ]


def wait_exit(pid: int) -> None:
    """Block until a process exits, however it ends. Returns at once if it's gone."""
    try:
        psutil.Process(pid).wait()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return


def created_at(pid: int) -> float | None:
    """When a running process started, in Unix seconds. None if it's gone.
    Raises if it runs but won't say (access denied): that is not the same as gone."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as error:
        raise PermissionError from error


KILL_WAIT_S = 10


def terminate(pid: int, timeout_s: float) -> None:
    """End a process and wait up to `timeout_s` for it to go. Nothing if it's
    gone; raises if it can't be ended or is still there after the wait."""
    try:
        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout_s)
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied as error:
        raise PermissionError from error
    except psutil.TimeoutExpired as error:
        raise TimeoutError from error


def kill_tree(pid: int) -> list[int]:
    """End a process and every process it started, wait up to KILL_WAIT_S for
    them to go, and return the pids still running. Ending only the process
    would orphan its children."""
    try:
        process = psutil.Process(pid)
        family = [*process.children(recursive=True), process]
    except psutil.NoSuchProcess:
        return []
    for member in family:
        # One that refuses must not stop the rest: it is reported as left.
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            member.kill()
    _, alive = psutil.wait_procs(family, timeout=KILL_WAIT_S)
    return [member.pid for member in alive]


def process_image(image: str) -> Path | None:
    for pid in process_ids(image):
        path = image_path(pid)
        if path is not None:
            return path
    return None
