"""The Windows Firewall rule sandbox and remote mode need, and the Default Switch address.

NVDA inside Windows Sandbox, or on a VM, connects out to the relay on the host's
Hyper-V Default Switch address (or remote mode's `host`). Inbound connections
there are blocked unless a rule allows them. `asc firewall-setup` creates one rule
per port, with your consent (a UAC prompt), as narrow as keeps working: inbound
TCP, only from this computer's local subnets, only the relay port, only for the
program that runs the daemon.

The rule names neither the Default Switch nor its address. Windows removes the
switch when no sandbox runs and makes a new one, with a new id and address, for
the next sandbox, and a rule on an interface keeps the id of the old one (see
docs/findings.md). The relay itself listens only on the Default Switch address,
which is looked up at every start.
"""

from __future__ import annotations

import ipaddress
import os
import sys

from agent_speech_check.errors import FirewallFailed, NoDefaultSwitch, PowerShellTimedOut
from agent_speech_check.windows import winapi
from agent_speech_check.windows.powershell import encoded, quote, run

SANDBOX_RULE = "agent-speech-check sandbox relay"
INTERFACE = "vEthernet (Default Switch)"
UAC_TIMEOUT_S = 300


def rule_name(mode: str, port: int) -> str:
    """Sandbox mode keeps one rule, whatever its port. Remote mode has one per
    port, so each instance can have its own."""
    return SANDBOX_RULE if mode == "sandbox" else f"agent-speech-check remote relay {int(port)}"


def create_script(rule: str, port: int, program: str) -> str:
    return (
        f"Remove-NetFirewallRule -DisplayName {quote(rule)} -ErrorAction SilentlyContinue; "
        f"New-NetFirewallRule -DisplayName {quote(rule)} -Direction Inbound "
        f"-Action Allow -Protocol TCP -LocalPort {int(port)} "
        f"-RemoteAddress LocalSubnet -Program {quote(program)} -Profile Any "
        "| Out-Null"
    )


def check_script(rule: str, port: int, program: str) -> str:
    """Prints `ok` when the rule exists with this port and program."""
    return (
        f"$r = Get-NetFirewallRule -DisplayName {quote(rule)} -ErrorAction "
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


def rule_ok(rule: str, port: int, program: str) -> bool:
    return run(check_script(rule, port, program)).strip() == "ok"


def create_rule(rule: str, port: int, program: str) -> None:
    """Create the rule in an elevated PowerShell. Windows shows a UAC prompt."""
    inner = encoded(create_script(rule, port, program))
    outer = (
        "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
        f"'-NoProfile','-NonInteractive','-EncodedCommand','{inner}'"
    )
    try:
        # It waits for the UAC prompt, so give a person time to answer it.
        run(outer, timeout_s=UAC_TIMEOUT_S)
    except PowerShellTimedOut:
        raise FirewallFailed(rule) from None
    if not rule_ok(rule, port, program):
        raise FirewallFailed(rule)
