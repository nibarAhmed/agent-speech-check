"""One target's Windows Sandbox, from start to stop, for the daemon.

The sandbox gets three mapped folders (the start script, the NVDA installer,
and a writable config folder holding `nvda.ini`) plus the target's `share`
folders. Its NVDA connects out to the relay on the host's Default Switch
address. The window is kept minimised for the whole session.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from agent_speech_check import nvda, sandbox
from agent_speech_check.errors import AscError, LaunchFailed, SandboxFailed
from agent_speech_check.nvda_config import Relay, Setting, render

log = logging.getLogger(__name__)

MINIMIZE_EVERY_S = 0.5


class SandboxSession:
    def __init__(
        self, folder: Path, shares: tuple[str, ...], relay: Relay, settings: tuple[Setting, ...]
    ) -> None:
        self.folder = folder / "sandbox"
        self.shares = shares
        self.relay = relay
        self.settings = settings
        self.state = "starting"
        self.sandbox_id: str | None = None

    def _prepare(self) -> str:
        """Write the mapped folders and return the sandbox configuration."""
        if self.folder.exists():
            shutil.rmtree(self.folder)
        in_dir, config_dir = self.folder / "in", self.folder / "config"
        in_dir.mkdir(parents=True)
        config_dir.mkdir()
        installer = nvda.installer_path(nvda.VERSION)
        script = sandbox.nvda_script(installer.name)
        (in_dir / sandbox.SCRIPT).write_text(script, encoding="utf-8")
        (in_dir / sandbox.LAUNCH_SCRIPT).write_text(sandbox.LAUNCH_PS1, encoding="utf-8")
        ini = render(self.relay, self.settings)
        (config_dir / "nvda.ini").write_text(ini, encoding="utf-8")
        folders = sandbox.session_folders(
            str(in_dir), str(installer.parent), str(config_dir), self.shares
        )
        return sandbox.config_xml(folders, networking=True)

    def _start(self) -> None:
        config = self._prepare()
        self.sandbox_id = sandbox.start_minimized(config)
        log.info("sandbox %s started", self.sandbox_id)
        code = sandbox.exec_as_user(self.sandbox_id, sandbox.script_command())
        if code:
            raise SandboxFailed("exec", f"the NVDA start script exited with {code}")

    async def run(self, stopping: asyncio.Event) -> None:
        """Start the sandbox, then keep its window minimised until stopping."""
        try:
            await asyncio.to_thread(self._start)
        except AscError as error:
            self.state = f"failed: {error.message}"
            log.warning("sandbox did not start: %s", error.message)
            return
        self.state = "running"
        while not stopping.is_set():
            sandbox.keep_minimized()
            await asyncio.sleep(MINIMIZE_EVERY_S)

    async def launch(self, command: str) -> None:
        if self.sandbox_id is None:
            raise SandboxFailed("exec", "the sandbox is not running yet")
        code = await asyncio.to_thread(
            sandbox.exec_as_user, self.sandbox_id, sandbox.launch_command(command)
        )
        if code == sandbox.NO_WINDOW_EXIT:
            raise LaunchFailed(command, "no new window appeared in the sandbox within 30 s")
        if code:
            raise LaunchFailed(command, f"the launch script exited with {code}")

    async def stop(self) -> None:
        if self.sandbox_id is not None:
            try:
                await asyncio.to_thread(sandbox.stop, self.sandbox_id)
            except SandboxFailed as error:
                log.warning("sandbox stop: %s", error.message)
            self.sandbox_id = None
