"""Ask the user's own running NVDA to connect, for watch and shared mode.

While NVDA runs it registers the `nvdaremote://` link scheme for the current
user. Opening such a link hands it to the running NVDA, which asks the user to
confirm before it connects (see docs/protocol.md). That question is NVDA's and
asc never answers it: the user does. Nothing here starts NVDA or writes its
configuration.
"""

from __future__ import annotations

import os
import winreg
from urllib.parse import quote

# Under HKEY_CURRENT_USER, where NVDA registers the scheme.
HANDLER = r"Software\Classes\nvdaremote\shell\open\command"


def link(host: str, channel: str) -> str:
    """The link that makes NVDA the controlled side of `channel` on the relay at
    `host` (`address:port`)."""
    return f"nvdaremote://{host}?key={quote(channel, safe='')}&mode=slave"


def registered(command_key: str = HANDLER) -> bool:
    """Whether something takes the link. Without it, Windows would ask the user
    to pick an app."""
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, command_key).Close()
    except OSError:
        return False
    return True


def send(url: str) -> bool:
    """Hand the link to the running NVDA, and say whether Windows took it."""
    try:
        os.startfile(url)  # noqa: S606 - a link asc built, for NVDA's own handler
    except OSError:
        return False
    return True
