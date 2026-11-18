from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil
import pytest
from scripts.build_linux import write_tarball

from agent_speech_check.update import updater
from agent_speech_check.update.updater import Release

SHA = "0a466295ef00dab24316d85ee735ef25a692f9682066802244a4d0d31e1ed10e"
URL = "https://github.com/nibarAhmed/agent-speech-check/releases/download/v0.5.0/agent-speech-check-0.5.0.msi"


def github(tag: str = "v0.5.0", **asset: Any) -> dict[str, Any]:
    """GitHub's latest-release answer, cut down to what asc reads, plus noise."""
    msi = {"name": "agent-speech-check-0.5.0.msi", "browser_download_url": URL}
    msi["digest"] = f"sha256:{SHA}"
    msi.update(asset)
    manifest = {"name": "nibarAhmed.AgentSpeechCheck.yaml", "browser_download_url": URL}
    return {"tag_name": tag, "draft": False, "assets": [manifest, msi]}


def test_parse_release() -> None:
    assert updater.parse_release(github(), "win32") == Release((0, 5, 0), URL, SHA)
    assert updater.parse_release(github(digest=f"sha256:{SHA.upper()}"), "win32") == Release(
        (0, 5, 0), URL, SHA
    )


def test_parse_release_takes_the_asset_built_for_this_platform() -> None:
    tarball = URL.replace("agent-speech-check-0.5.0.msi", updater.LINUX_ASSET)
    answer = github()
    answer["assets"].append(
        {"name": updater.LINUX_ASSET, "browser_download_url": tarball, "digest": f"sha256:{SHA}"}
    )
    assert updater.parse_release(answer, "linux", "x86_64") == Release((0, 5, 0), tarball, SHA)
    assert updater.parse_release(answer, "win32", "ARM64") == Release((0, 5, 0), URL, SHA)
    assert updater.parse_release(answer, "linux", "aarch64") is None
    assert updater.parse_release(github(), "linux", "x86_64") is None


@pytest.mark.parametrize(
    "answer",
    [
        github(tag="0.5.0"),
        github(tag="v0.5"),
        github(tag="v0.5.0-rc1"),
        github(digest=None),
        github(digest=f"sha1:{SHA[:40]}"),
        github(name="agent-speech-check-0.4.0.msi"),
        github(browser_download_url=URL.replace("nibarAhmed", "someone")),
        github(browser_download_url=URL.replace("https", "http")),
        {"tag_name": "v0.5.0"},
        {"message": "API rate limit exceeded"},
        [],
    ],
)
def test_parse_release_refuses_what_it_cannot_trust(answer: object) -> None:
    assert updater.parse_release(answer, "win32") is None


def test_newer() -> None:
    release = Release((0, 5, 0), URL, SHA)
    assert updater.newer(release, "0.4.0")
    assert updater.newer(release, "0.4.10")
    assert not updater.newer(release, "0.5.0")
    assert not updater.newer(release, "0.10.0")
    assert not updater.newer(release, "unknown")


def test_due(tmp_path: Path) -> None:
    stamp = tmp_path / "checked"
    assert updater.due(stamp, 1_000_000)
    stamp.touch()
    os.utime(stamp, (1_000_000, 1_000_000))
    assert not updater.due(stamp, 1_000_000 + updater.CHECK_EVERY_S - 1)
    assert updater.due(stamp, 1_000_000 + updater.CHECK_EVERY_S)
    # A clock set back must not stop the checks for good.
    assert updater.due(stamp, 1_000_000 - 1)
    # Deferred because asc ran: due again in an hour, not a day.
    updater.defer(stamp, 2_000_000)
    assert not updater.due(stamp, 2_000_000 + updater.RETRY_S - 1)
    assert updater.due(stamp, 2_000_000 + updater.RETRY_S)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """asc's state folders, under `tmp_path` on Windows and Linux alike."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


@pytest.mark.usefixtures("state_home")
def test_enabled_unless_any_instance_turns_it_off(tmp_path: Path) -> None:
    home = tmp_path / "agent-speech-check"
    vm1 = home / "instances" / "vm1"
    vm1.mkdir(parents=True)
    assert updater.enabled()
    (home / "config.toml").write_text("[defaults]\nauto_update = true\n", encoding="utf-8")
    assert updater.enabled()
    (vm1 / "config.toml").write_text("[defaults]\nauto_update = false\n", encoding="utf-8")
    assert not updater.enabled()
    # Unreadable: it may say false, so no update.
    (vm1 / "config.toml").write_text("[defaults\n", encoding="utf-8")
    assert not updater.enabled()


@pytest.mark.usefixtures("state_home")
def test_installing_only_while_that_process_lives() -> None:
    assert not updater.installing()
    child = subprocess.Popen([sys.executable, "-c", "input()"], stdin=subprocess.PIPE)
    try:
        updater.mark(updater.marker(), child.pid)
        assert updater.installing()
    finally:
        child.kill()
        child.wait()
    assert not updater.installing()
    # The same pid, reused by another process, isn't the installer.
    updater.marker().write_text(
        f'{{"pid": {os.getpid()}, "started": {psutil.Process().create_time() - 1}}}',
        encoding="utf-8",
    )
    assert not updater.installing()
    updater.marker().write_text("not json", encoding="utf-8")
    assert not updater.installing()


@pytest.mark.usefixtures("state_home")
def test_download_keeps_only_an_msi_with_the_listed_sha256(tmp_path: Path) -> None:
    source = tmp_path / "served.msi"
    source.write_bytes(b"msi")
    good = hashlib.sha256(b"msi").hexdigest()
    # One an earlier run left, cut off mid-download.
    updater.folder().mkdir(parents=True)
    (updater.folder() / "agent-speech-check-0.4.0.part").write_bytes(b"ms")
    assert updater.download(Release((0, 5, 0), source.as_uri(), SHA)) is None
    assert not list(updater.folder().iterdir())
    msi = updater.download(Release((0, 5, 0), source.as_uri(), good))
    assert msi is not None
    assert msi.read_bytes() == b"msi"


def installed(root: Path, *versions: str) -> Path:
    """A Linux install at `root` with these versions, `current` at the last one: its asc."""
    for version in versions:
        (root / "versions" / version).mkdir(parents=True)
        (root / "versions" / version / "asc").write_bytes(version.encode())
    (root / "current").symlink_to(Path("versions") / versions[-1])
    return root / "versions" / versions[-1] / "asc"


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_install_root_is_where_the_tarball_was_unpacked(tmp_path: Path) -> None:
    exe = installed(tmp_path / "agent-speech-check", "0.4.0")
    assert updater.install_root(exe) == tmp_path / "agent-speech-check"
    (tmp_path / "loose").mkdir()
    assert updater.install_root(tmp_path / "loose" / "asc") is None
    (tmp_path / "other" / "versions" / "0.4.0").mkdir(parents=True)
    assert updater.install_root(tmp_path / "other" / "versions" / "0.4.0" / "asc") is None


@pytest.mark.skipif(sys.platform != "linux", reason="the Linux install's layout")
def test_unpack_switches_current_and_keeps_only_what_may_run(tmp_path: Path) -> None:
    root = tmp_path / "agent-speech-check"
    exe = installed(root, "0.3.0", "0.4.0")
    # What a crash mid-update left: a half-unpacked tarball and a half-made link.
    (root / ".unpacking" / "junk").mkdir(parents=True)
    (root / ".current").symlink_to("nowhere")
    app = tmp_path / "app"
    app.mkdir()
    (app / "asc").write_bytes(b"0.5.0")
    tarball = tmp_path / updater.LINUX_ASSET
    write_tarball(app, "0.5.0", tarball)

    updater.unpack(tarball, exe, "0.5.0")

    assert (root / "current" / "asc").read_bytes() == b"0.5.0"
    assert (root / "current").readlink() == Path("versions", "0.5.0")
    # The version this process runs from stays; the older one goes.
    assert sorted(path.name for path in (root / "versions").iterdir()) == ["0.4.0", "0.5.0"]
    assert sorted(path.name for path in root.iterdir()) == ["current", "versions"]
