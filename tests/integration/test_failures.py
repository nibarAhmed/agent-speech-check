"""What a running wait or launch does when a connection fails or NVDA goes away.

Only NVDA's own joined connection can fail a command. A connection that never
joins can't be told apart from a stranger's, so its failure shows only in
`asc status`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from tests.fake_nvda import FakeNvda
from tests.integration.conftest import Env, run_asc, start_asc, tls_client, wait_until


@pytest.fixture
def nvda(env: Env) -> Iterator[FakeNvda]:
    assert run_asc("start", "--mode", "desktop").returncode == 0
    fake = env.fake("desktop")
    yield fake
    fake.close()


def test_status_lists_the_commands_in_progress(env: Env, nvda: FakeNvda) -> None:
    assert env.running("desktop") == []
    waiting = start_asc("wait", "later", "--timeout", "8000")
    env.wait_running("desktop", "wait")
    assert env.running("desktop") == ["wait"]
    nvda.say("later")
    assert 'said: "later"' in waiting.communicate(timeout=30)[0]
    assert env.running("desktop") == []


def test_a_strangers_bad_connection_does_not_fail_a_wait(env: Env, nvda: FakeNvda) -> None:
    waiting = start_asc("wait", "later", "--timeout", "8000")
    env.wait_running("desktop", "wait")
    with tls_client(env.port) as sock:
        sock.sendall(b"{not json\n")
        assert sock.recv(4096) == b""
    nvda.say("later")
    out, err = waiting.communicate(timeout=30)
    assert waiting.returncode == 0, err
    assert 'said: "later"' in out
    status = run_asc("status").stdout
    assert "last error: NVDA sent a line that is not a JSON object" in status


@pytest.mark.xdist_group("desktop")
@pytest.mark.parametrize("command", [("wait", "never"), ("launch", f'"{sys.executable}" -c pass')])
def test_a_protocol_fault_from_nvda_fails_wait_and_launch(
    env: Env, nvda: FakeNvda, command: tuple[str, str]
) -> None:
    running = start_asc(*command, "--timeout", "8000")
    env.wait_running("desktop", command[0])
    nvda.send_raw(b"{not json\n")
    _, err = running.communicate(timeout=30)
    assert running.returncode == 5, err
    assert "NVDA sent a line that is not a JSON object" in err


def test_nvda_that_goes_and_stays_away_fails_a_wait_with_3(env: Env, nvda: FakeNvda) -> None:
    waiting = start_asc("wait", "never", "--timeout", "3000")
    env.wait_running("desktop", "wait")
    nvda.close()
    _, err = waiting.communicate(timeout=30)
    assert waiting.returncode == 3, err
    assert "NVDA is not connected after waiting 3000 ms" in err


def test_a_rejected_message_from_nvda_keeps_its_values_out_of_the_log(
    env: Env, nvda: FakeNvda
) -> None:
    # Speech in the wrong shape: a string where NVDA sends a list.
    nvda.send({"type": "speak", "sequence": "my password"})
    log = env.folder("desktop") / "daemon.log"
    assert wait_until(lambda: "dropped a connection" in log.read_text(encoding="utf-8"))
    assert "my password" not in log.read_text(encoding="utf-8")
