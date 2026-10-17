"""Sandbox mode's Windows Sandbox, from start to stop, for the daemon.

The sandbox gets three mapped folders (the start script, the NVDA installer or
your own portable NVDA, and a writable config folder) plus sandbox mode's `share`
folders. Its NVDA connects out to the relay on the host's Default Switch
address. For the whole session the window is kept hidden behind a tray icon
that shows and hides it ("tray", the default), minimised ("taskbar"), or left
alone ("show").
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from agent_speech_check.config.targets import Target
from agent_speech_check.errors import AscError, LaunchFailed, SandboxBroken, SandboxFailed
from agent_speech_check.nvda import launcher, nvda_config
from agent_speech_check.nvda.nvda_config import RelayAddress
from agent_speech_check.sandbox import wsb

log = logging.getLogger(__name__)

# Outside `sandbox/`, which the next start deletes while a guard may still write.
GUARD_ERR = "sandbox_guard.err"


class SandboxSession:
    def __init__(self, folder: Path, target: Target, relay: RelayAddress) -> None:
        self.folder = folder / "sandbox"
        self.shares = target.share
        self.relay = relay
        self.settings = target.settings
        self.nvda_dir = Path(target.nvda_dir) if target.nvda_dir else None
        self.state = "starting"
        self.failure: AscError | None = None
        self.sandbox_id: str | None = None
        self.window = wsb.SandboxWindow(target.sandbox_window)

    def _prepare(self) -> str:
        """Write the mapped folders and return the sandbox configuration."""
        if self.folder.exists():
            shutil.rmtree(self.folder)
        in_dir, config_dir = self.folder / "in", self.folder / "config"
        in_dir.mkdir(parents=True)
        if self.nvda_dir is None:
            installer = launcher.installer_path(launcher.VERSION)
            script = wsb.nvda_script(installer.name)
            mapped, source = installer.parent, None
        else:
            script = wsb.nvda_script(None)
            mapped, source = self.nvda_dir, nvda_config.source_config(self.nvda_dir)
        (in_dir / wsb.SCRIPT).write_text(script, encoding="utf-8")
        (in_dir / wsb.LAUNCH_SCRIPT).write_text(wsb.LAUNCH_PS1, encoding="utf-8")
        nvda_config.write(config_dir, self.relay, self.settings, source)
        # Store Python redirects %LOCALAPPDATA% for itself only; wsb needs the
        # real folders. realpath finds them, but only once they exist.
        in_dir, mapped, config_dir = (
            Path(os.path.realpath(p)) for p in (in_dir, mapped, config_dir)
        )
        folders = wsb.session_folders(str(in_dir), str(mapped), str(config_dir), self.shares)
        return wsb.config_xml(folders, networking=True)

    def _start(self) -> None:
        config = self._prepare()
        self.sandbox_id = wsb.start_connected(config, self.window, self.folder.parent / GUARD_ERR)
        log.info("sandbox %s started", self.sandbox_id)
        code = wsb.exec_as_user(self.sandbox_id, wsb.script_command())
        if code:
            raise SandboxBroken("exec", f"the NVDA start script exited with {code}")

    async def run(self) -> None:
        """Start the sandbox. Its window is kept in place until `stop`."""
        # Off the loop: adding the tray icon waits on its thread for up to 5 s.
        await asyncio.to_thread(self.window.start)
        try:
            await asyncio.to_thread(self._start)
        except AscError as error:
            self.failure = error
            self.state = f"failed: {error.message}"
            log.warning("sandbox did not start: %s", error.message)
            await asyncio.to_thread(self.window.stop)
            return
        self.state = "running"

    async def launch(self, command: str) -> None:
        if self.sandbox_id is None:
            raise SandboxFailed("exec", "the sandbox is not running yet")
        code = await asyncio.to_thread(
            wsb.exec_as_user, self.sandbox_id, wsb.launch_command(command)
        )
        if code == wsb.NO_WINDOW_EXIT:
            raise LaunchFailed(command, "no new window appeared in the sandbox within 30 s")
        if code:
            raise LaunchFailed(command, f"the launch script exited with {code}")

    async def stop(self) -> None:
        if self.sandbox_id is not None:
            try:
                await asyncio.to_thread(wsb.stop, self.sandbox_id)
            except wsb.FAILURES as error:
                log.warning("sandbox stop: %s", error.message)
            self.sandbox_id = None
        # Only now: the client shows its window again as the sandbox stops.
        # Off the loop: it joins the keeping and tray threads.
        await asyncio.to_thread(self.window.stop)
