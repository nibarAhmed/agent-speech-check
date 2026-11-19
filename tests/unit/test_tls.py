from __future__ import annotations

import contextlib
import socket
import ssl
import threading
import urllib.error
from pathlib import Path

import pytest

from agent_speech_check.remote import relay
from agent_speech_check.system import tls


def test_https_refuses_a_certificate_windows_does_not_trust(tmp_path: Path) -> None:
    credentials = relay.load_or_create(tmp_path)
    context = relay.server_context(credentials.cert, credentials.key)
    with socket.create_server(("127.0.0.1", 0)) as server:

        def handshake() -> None:
            connection, _ = server.accept()
            with connection, contextlib.suppress(OSError):
                context.wrap_socket(connection, server_side=True).close()

        thread = threading.Thread(target=handshake, daemon=True)
        thread.start()
        with pytest.raises(urllib.error.URLError) as refused:
            tls.urlopen(f"https://127.0.0.1:{server.getsockname()[1]}/", 5)
        thread.join(5)
    assert isinstance(refused.value.reason, ssl.SSLCertVerificationError)
