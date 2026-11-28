import json
from pathlib import Path

import pytest

from agent_speech_check.errors import OperationalError
from agent_speech_check.system import machine, winapi

_KEY = "debug1: Server host key: ssh-ed25519 SHA256:"


def test_the_machine_is_its_last_host_key() -> None:
    # With ProxyJump the jump host's key comes first.
    lines = [f"{_KEY}jump", "debug1: Authenticated to jump", f"{_KEY}vm", "debug1: Exit status 0"]
    assert machine.host_key("asc@vm", lines) == "ssh-ed25519 SHA256:vm"
    # A host certificate's line goes on with its serial, ID and CA, which change on renewal.
    cert = (
        "debug1: Server host certificate: ssh-ed25519-cert-v01@openssh.com SHA256:vm,"
        ' serial 7 ID "vm" CA ssh-ed25519 SHA256:ca valid forever'
    )
    assert (
        machine.host_key("asc@vm", [f"{_KEY}jump", cert])
        == "ssh-ed25519-cert-v01@openssh.com SHA256:vm"
    )
    with pytest.raises(OperationalError, match="host key"):
        machine.host_key("asc@vm", ["debug1: Exit status 0"])


def _probe(hkl: int, preload: str | None, scans: dict[str, int]) -> bytes:
    found = [scans.get(char, -1) for char in machine.CHARS]
    return json.dumps({"hkl": hkl, "preload": preload, "scans": found}).encode()


def test_the_layout_maps_what_its_keys_type() -> None:
    probe = _probe(0x04090409, "00000409", {":": 0x1BA, "\\": 0xDC, "C": 0x143})
    assert machine.layout(probe) == {":": (0xBA, 1), "\\": (0xDC, 0), "C": (0x43, 1)}


@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        # ssh sessions there may use another layout than the user's desktop does.
        (_probe(0x04090409, "00000414", {}), "ssh sessions there use 04090409, not 00000414"),
        (_probe(0x04090409, "00010409", {}), "ssh sessions there use 04090409, not 00010409"),
        (_probe(0x04090409, None, {}), "no default keyboard layout"),
        (b"#< CLIXML", "not the keyboard layout"),
        (json.dumps({"hkl": 0x04090409, "preload": "00000409", "scans": [1]}).encode(), "scans"),
    ],
)
def test_a_layout_asc_cant_trust_says_why(probe: bytes, reason: str) -> None:
    with pytest.raises(OperationalError, match=reason):
        machine.layout(probe)


def test_one_instance_at_a_time_holds_a_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for home in ("LOCALAPPDATA", "XDG_STATE_HOME"):
        monkeypatch.setenv(home, str(tmp_path))
    vm = "ssh-ed25519 SHA256:vm"
    held = [machine.hold(vm, "ui-arrows", "asc@vm")]
    try:
        with pytest.raises(OperationalError, match="asc@vm is in use by instance ui-arrows"):
            machine.hold(vm, "chatui", "asc@vm")
        held.append(machine.hold("ssh-ed25519 SHA256:other", "chatui", "asc@other"))
    finally:
        for lock in held:
            if isinstance(lock, int):
                winapi.close_handle(lock)
            else:
                lock.close()
