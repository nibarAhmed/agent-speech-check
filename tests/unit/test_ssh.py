import asyncio
from pathlib import Path

import pytest

from agent_speech_check.errors import PortInUse, SshFailed, SshMissing, SshSetupNeeded
from agent_speech_check.remote import ssh


def test_tunnel_command() -> None:
    assert ssh.tunnel_command("me@vm", 6837, 50123) == [
        *("ssh", "-v", "-N", "-o", "ExitOnForwardFailure=yes"),
        *("-R", "127.0.0.1:6837:127.0.0.1:50123"),
        *("-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"),
        *("--", "me@vm"),
    ]


@pytest.mark.parametrize(
    ("lines", "error", "detail"),
    [
        (["Host key verification failed."], SshSetupNeeded, "Host key verification failed."),
        (
            ["Permission denied (publickey).", "Connection closed"],
            SshSetupNeeded,
            "Connection closed",
        ),
        (["connect to host vm port 22: Connection refused"], SshFailed, "Connection refused"),
        (
            [
                "OpenSSH_9.6p1, OpenSSL 3.0.13",
                'Authenticated to vm ([10.0.0.2]:22) using "publickey".',
                "Timeout, server vm not responding.",
                "Transferred: sent 2964, received 2580 bytes, in 3.2 seconds",
                "Bytes per second: sent 921.4, received 802.2",
            ],
            SshFailed,
            "Timeout, server vm not responding.",
        ),
        ([], SshFailed, "exit code 255"),
    ],
)
def test_why_ssh_failed(lines: list[str], error: type, detail: str) -> None:
    failed = ssh.failed("me@vm", lines, 255)
    assert type(failed) is error
    assert failed.message.startswith("ssh me@vm failed: ")
    assert detail in failed.message


def test_a_taken_port_on_the_other_machine() -> None:
    tunnel = ssh.Tunnel("me@vm", 6837, 50123)
    lines = ["Error: remote port forwarding failed for listen port 6837"]
    error = tunnel.error(255, lines)
    assert isinstance(error, PortInUse)
    assert "6837" in error.message
    assert isinstance(tunnel.error(255, ["Connection closed by 10.0.0.2 port 22"]), SshFailed)


def test_no_ssh_on_this_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    tunnel = ssh.Tunnel("me@vm", 6837, 50123)
    asyncio.run(tunnel.run())
    assert isinstance(tunnel.failure, SshMissing)
    assert tunnel.state() == {"up": False, "error": tunnel.failure.message}
