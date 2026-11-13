import base64

import pytest

from agent_speech_check.errors import PowerShellTimedOut
from agent_speech_check.sandbox import firewall
from agent_speech_check.windows import powershell


def test_create_script_is_narrow() -> None:
    script = firewall.create_script(firewall.SANDBOX_RULE, 16837, r"C:\Program Files\asc\asc.exe")
    assert "-Direction Inbound" in script
    assert "-Protocol TCP -LocalPort 16837" in script
    assert "-RemoteAddress LocalSubnet" in script
    assert r"-Program 'C:\Program Files\asc\asc.exe'" in script
    assert "-Action Allow" in script
    # Windows replaces the Default Switch between sandboxes, and a rule on an
    # interface keeps the old one's id, so it would match nothing.
    assert "InterfaceAlias" not in script
    # Re-running replaces the rule instead of adding a second one.
    assert script.startswith(
        "Remove-NetFirewallRule -DisplayName 'agent-speech-check sandbox relay'"
    )


def test_quotes_cannot_break_out() -> None:
    script = firewall.create_script(firewall.rule_name("remote", 1), 1, "C:\\it's\\x.exe")
    assert "-Program 'C:\\it''s\\x.exe'" in script
    # Remote mode has one rule per port, so each instance can have its own.
    assert "-DisplayName 'agent-speech-check remote relay 1'" in script


def test_check_script_names_port_and_program() -> None:
    script = firewall.check_script(firewall.SANDBOX_RULE, 16837, "C:\\p.exe")
    assert "LocalPort -eq '16837'" in script
    assert "Program -eq 'C:\\p.exe'" in script


def test_parse_address() -> None:
    assert firewall.parse_address("172.24.80.1\r\n") == "172.24.80.1"
    assert firewall.parse_address("\nnoise\n10.0.0.2\n") == "10.0.0.2"
    assert firewall.parse_address("") is None
    assert firewall.parse_address("fe80::1") is None


def test_parse_ics_name() -> None:
    hosts = (
        "# Copyright (c) 1993-2001 Microsoft Corp.\r\n\r\n"
        "172.25.151.95 77dc45fb-f87d.mshome.net # 2026 11 3 18 21 53 57 307\r\n"
        "172.24.64.1 DESKTOP-E1Q2MEQ.mshome.net # 2031 11 2 11 23 51 28 356\r\n"
    )
    # NVDA lowercases the host before it looks up the certificate it trusts.
    assert firewall.parse_ics_name(hosts, "172.24.64.1") == "desktop-e1q2meq.mshome.net"
    assert firewall.parse_ics_name(hosts, "172.24.64.11") is None
    assert firewall.parse_ics_name("", "172.24.64.1") is None


def test_encoded_is_utf16_base64() -> None:
    assert base64.b64decode(powershell.encoded("hi")).decode("utf-16-le") == "hi"


def test_run_that_outlasts_its_timeout_is_an_asc_error() -> None:
    with pytest.raises(PowerShellTimedOut, match="within 1 s"):
        powershell.run("Start-Sleep -Seconds 10", timeout_s=1)
