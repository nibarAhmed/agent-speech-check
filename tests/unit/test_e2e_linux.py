from __future__ import annotations

from scripts.e2e_linux import image, step


def test_the_container_is_e2e_ymls_orca_job() -> None:
    assert image().startswith("ubuntu:")
    assert "apt-get install" in step("Install Orca and what asc runs with it")
    assert "/usr/sbin/sshd" in step("Let root ssh to localhost with a key")
    assert step("Orca and ssh tests").startswith("uv run pytest tests/unit/test_orca.py ")
