from __future__ import annotations

import pytest
from scripts.build_installer import msi_version


@pytest.mark.parametrize("version", ["0.0.1", "1.2.3", "255.255.65535"])
def test_msi_version_keeps_a_version_windows_installer_accepts(version: str) -> None:
    assert msi_version(version) == version


@pytest.mark.parametrize(
    "version",
    ["1.2", "1.2.3.4", "1.2.3a1", "256.0.0", "0.256.0", "0.0.65536", chr(0x0661) + ".2.3"],
)
def test_msi_version_refuses_what_windows_installer_cannot_use(version: str) -> None:
    with pytest.raises(SystemExit, match="An MSI needs a version like"):
        msi_version(version)
