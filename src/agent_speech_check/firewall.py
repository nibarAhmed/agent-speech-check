"""The one Windows Firewall rule sandbox mode needs, and the Default Switch address.

NVDA inside Windows Sandbox connects out to the relay on the host's Hyper-V
Default Switch address. Inbound connections there are blocked unless a rule
allows them. `asc sandbox-setup` creates one rule, with your consent (a UAC
prompt), as narrow as Windows allows: inbound TCP, only on the Default Switch
interface, only the relay port, only for the program that runs the daemon.
The address can change at reboot, so it is looked up at every start and the
rule never names it.
"""

from __future__ import annotations

import ipaddress
import os
import sys

from agent_speech_check import winapi
from agent_speech_check.errors import FirewallFailed, NoDefaultSwitch
from agent_speech_check.powershell import encoded, quote, run

RULE_NAME = "agent-speech-check sandbox relay"
INTERFACE = "vEthernet (Default Switch)"


def create_script(port: int, program: str) -> str:
    return (
        f"Remove-NetFirewallRule -DisplayName {quote(RULE_NAME)} -ErrorAction SilentlyContinue; "
        f"New-NetFirewallRule -DisplayName {quote(RULE_NAME)} -Direction Inbound "
        f"-Action Allow -Protocol TCP -LocalPort {int(port)} "
        f"-InterfaceAlias {quote(INTERFACE)} -Program {quote(program)} -Profile Any "
        "| Out-Null"
    )


def check_script(port: int, program: str) -> str:
    """Prints `ok` when the rule exists with this port and program."""
    return (
        f"$r = Get-NetFirewallRule -DisplayName {quote(RULE_NAME)} -ErrorAction "
        "SilentlyContinue; if ($r -and $r.Enabled -eq 'True' -and "
        f"($r | Get-NetFirewallPortFilter).LocalPort -eq '{int(port)}' -and "
        f"($r | Get-NetFirewallApplicationFilter).Program -eq {quote(program)}) "
        "{ 'ok' } else { 'missing' }"
    )


def address_script() -> str:
    return (
        f"(Get-NetIPAddress -InterfaceAlias {quote(INTERFACE)} -AddressFamily IPv4 "
        "-ErrorAction SilentlyContinue).IPAddress"
    )


def parse_address(output: str) -> str | None:
    for line in output.splitlines():
        try:
            return str(ipaddress.IPv4Address(line.strip()))
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- the thin shell


def daemon_program() -> str:
    """The exe that will own the relay socket: this process's real image.

    Under a venv, `python.exe` is a small launcher and the real interpreter is
    a child process, so the venv path would not match the socket's owner.
    """
    image = winapi.image_path(os.getpid())
    return str(image) if image is not None else sys.executable


def default_switch_address() -> str:
    address = parse_address(run(address_script()))
    if address is None:
        raise NoDefaultSwitch(INTERFACE)
    return address


def rule_ok(port: int, program: str) -> bool:
    return run(check_script(port, program)).strip() == "ok"


def create_rule(port: int, program: str) -> None:
    """Create the rule in an elevated PowerShell. Windows shows a UAC prompt."""
    inner = encoded(create_script(port, program))
    outer = (
        "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
        f"'-NoProfile','-NonInteractive','-EncodedCommand','{inner}'"
    )
    run(outer)
    if not rule_ok(port, program):
        raise FirewallFailed(RULE_NAME)
