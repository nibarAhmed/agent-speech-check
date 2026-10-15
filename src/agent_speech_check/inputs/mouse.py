"""The one place input goes to Windows itself rather than to NVDA: a still mouse.

After recent input Windows keeps a program started from a background process
(the daemon) behind the window in front. It still tells NVDA the program came
forward, NVDA finds it didn't and drops it, and no later switch reaches NVDA
reliably. A process that sent the last input may hand on the right to come
forward, so the daemon sends a mouse input that moves nothing just before it
starts an app (observed in Windows Sandbox: docs/protocol.md, the foreground lock).
"""

from __future__ import annotations

from agent_speech_check.windows import winapi


def nudge() -> bool:
    """Let the next app started take the foreground. False if Windows refused."""
    return winapi.send_still_mouse() and winapi.allow_foreground()
