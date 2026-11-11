"""The link that asks the user's own NVDA to connect. Opening it is tested
against a real NVDA (tests/e2e), never here: it would reach the NVDA that runs."""

from __future__ import annotations

import uuid
import winreg

from agent_speech_check.config.targets import Config, defaults
from agent_speech_check.nvda import invite


def test_link_names_the_relay_the_key_and_the_controlled_side() -> None:
    link = invite.link("127.0.0.1:6837", "aB3-_xyz")
    assert link == "nvdaremote://127.0.0.1:6837?key=aB3-_xyz&mode=slave"


def test_link_escapes_a_key_that_would_break_the_query() -> None:
    assert invite.link("127.0.0.1:1", "a&mode=master b") == (
        "nvdaremote://127.0.0.1:1?key=a%26mode%3Dmaster%20b&mode=slave"
    )


def test_registered_only_when_the_link_scheme_has_a_command() -> None:
    # Its own key, so the scheme NVDA registered on this machine is left alone.
    scheme = rf"Software\Classes\asc-test-{uuid.uuid4().hex}"
    command = rf"{scheme}\shell\open\command"
    assert not invite.registered(command)
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, command).Close()
    try:
        assert invite.registered(command)
    finally:
        for key in (command, rf"{scheme}\shell\open", rf"{scheme}\shell", scheme):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def test_only_watch_and_shared_mode_use_your_own_nvda() -> None:
    own = {mode: defaults(mode).own_nvda for mode in ("watch", "desktop", "sandbox", "shared")}
    assert own == {"watch": True, "desktop": False, "sandbox": False, "shared": True}


def test_connect_is_on_unless_set_off() -> None:
    assert Config.from_data({}, "config.toml").value("connect") == (True, "default")
    off = Config.from_data({"defaults": {"connect": False}}, "config.toml")
    assert off.value("connect") == (False, "global")
