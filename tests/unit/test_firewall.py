import base64

from agent_speech_check import firewall


def test_create_script_is_narrow() -> None:
    script = firewall.create_script(16837, r"C:\Program Files\asc\asc.exe")
    assert "-Direction Inbound" in script
    assert "-Protocol TCP -LocalPort 16837" in script
    assert "-InterfaceAlias 'vEthernet (Default Switch)'" in script
    assert r"-Program 'C:\Program Files\asc\asc.exe'" in script
    assert "-Action Allow" in script
    assert "RemoteAddress" not in script
    # Re-running replaces the rule instead of adding a second one.
    assert script.startswith(
        "Remove-NetFirewallRule -DisplayName 'agent-speech-check sandbox relay'"
    )


def test_quotes_cannot_break_out() -> None:
    script = firewall.create_script(1, "C:\\it's\\x.exe")
    assert "-Program 'C:\\it''s\\x.exe'" in script


def test_check_script_names_port_and_program() -> None:
    script = firewall.check_script(16837, "C:\\p.exe")
    assert "LocalPort -eq '16837'" in script
    assert "Program -eq 'C:\\p.exe'" in script


def test_parse_address() -> None:
    assert firewall.parse_address("172.24.80.1\r\n") == "172.24.80.1"
    assert firewall.parse_address("\nnoise\n10.0.0.2\n") == "10.0.0.2"
    assert firewall.parse_address("") is None
    assert firewall.parse_address("fe80::1") is None


def test_encoded_is_utf16_base64() -> None:
    assert base64.b64decode(firewall.encoded("hi")).decode("utf-16-le") == "hi"
