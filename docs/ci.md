# Using `asc` in CI

Use [desktop mode](cli.md#desktop-mode) in CI: `asc` starts its own screen reader (portable NVDA on Windows, Orca on Linux) and stops it when `asc stop` runs. A failed `--expect` or `asc wait` (or a timed-out `asc launch`) exits 1, which fails the job. See [Exit codes](cli.md#exit-codes).

## GitHub Actions setup action

Add `nibarAhmed/agent-speech-check@vX.Y.Z` to install `asc` (and a portable NVDA on Windows) and add it to `PATH` for later steps:

| Input | Default | Meaning |
|---|---|---|
| `version` | `latest` | `asc` release to install, like `1.2.3` or `v1.2.3`, or `latest` |
| `nvda` | `latest` | Windows only: NVDA version `asc get-nvda` installs, like `2026.2`, or `latest` |

- **Minimum release:** the action ships in `v0.8.2` and later, and installs `asc` 0.8.1 or later (older releases have no `SHA256SUMS`).
- **Pin both versions:** replace `vX.Y.Z` and `X.Y.Z` in the examples below with the same release tag and version number so the action and the installed `asc` stay in sync.
- **What it does:** it installs that release from GitHub over HTTPS, checks its SHA-256 against the release's `SHA256SUMS`, puts `asc` on `PATH`, and on Windows runs `asc get-nvda`. Other runner OSes fail.

## Windows example

```yaml
jobs:
  accessibility-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v7
      - name: Build app
        run: echo "Build your app here"
      - uses: nibarAhmed/agent-speech-check@vX.Y.Z
        with:
          version: X.Y.Z
      - name: Check screen reader speech
        run: |
          $PSNativeCommandUseErrorActionPreference = $true
          asc start --mode desktop
          asc launch ".\dist\myapp.exe" --title "My App"
          asc press Tab --expect "Save button"
          asc press Enter --expect "Saved"
      - if: failure()
        run: asc speech --since 0
      - if: always()
        run: asc stop
```

`pwsh` is the default shell on Windows runners, and by default a failed native command does not stop the script. Set `$PSNativeCommandUseErrorActionPreference = $true` at the top of the script, or run one `asc` command per step.

Keep the `if: failure()` step before `asc stop`: `asc stop` clears the speech log.

## Linux example

Linux desktop mode requires **Orca 51 or later** (see [Install](../README.md#install) and [Desktop mode](cli.md#desktop-mode)). Run the job in a newer Ubuntu container with `options: --init` so exited `orca` processes are reaped: Orca refuses to start while a zombie `orca` process exists.

```yaml
jobs:
  accessibility-linux:
    runs-on: ubuntu-latest
    container:
      image: ubuntu:26.10
      options: --init
    steps:
      - name: Install Orca and what asc runs with it
        env:
          DEBIAN_FRONTEND: noninteractive
        run: |
          apt-get update
          apt-get install -y --no-install-recommends \
            orca xvfb xdotool dbus-daemon libglib2.0-bin at-spi2-core openbox python3 \
            curl ca-certificates
      - uses: actions/checkout@v7
      - name: Build app
        run: echo "Build your app here"
      - uses: nibarAhmed/agent-speech-check@vX.Y.Z
        with:
          version: X.Y.Z
      - name: Check screen reader speech
        run: |
          asc start --mode desktop
          asc launch "./build/myapp" --title "My App"
          asc press Tab --expect "Save button"
          asc press Enter --expect "Saved"
      - if: failure()
        run: asc speech --since 0
      - if: always()
        run: asc stop
```

On Linux, desktop mode sends keys to a hidden Xvfb display, so no panic key matters.

## Writing checks

Within one flow, stop at the first failure. After an unexpected result, focus is somewhere unknown, so later checks would mean nothing. Put each independent flow in its own script or test, starting the app fresh each time.

Once you have more than a few flows, drive `asc` from your test framework. With `pytest`, start and stop `asc` once per session, launch the app per test, and close it in teardown:

```python
import subprocess
import pytest


def asc(*args: str) -> str:
    result = subprocess.run(["asc", *args], capture_output=True, text=True)
    assert result.returncode == 0, (
        f"asc {' '.join(args)} failed (exit {result.returncode}):\n{result.stdout}{result.stderr}"
    )
    return result.stdout


@pytest.fixture(scope="session", autouse=True)
def screen_reader():
    asc("start", "--mode", "desktop")
    try:
        yield
    finally:
        subprocess.run(["asc", "stop"], check=False)


def test_save_dialog():
    try:
        asc("launch", "./myapp", "--title", "My App")
        asc("press", "Tab", "--expect", "Save button")
        asc("press", "Enter", "--expect", "Saved")
    finally:
        # Closes whatever has focus: the app, if it opened.
        subprocess.run(["asc", "press", "Alt+F4"], check=False)
```

## Runners

- **GitHub-hosted runners:** `windows-latest` works as is. On Linux, use a container with Orca 51 or later, as in the Linux example.
- **Self-hosted Windows runners:** the runner must run in a logged-in, unlocked desktop session, not as a Windows service. A service has no desktop, so NVDA hears nothing and keys go nowhere. Configure auto-logon and disable screen lock and screensavers. Windows desktop mode also refuses to start while any `nvda.exe` is running, so do not run it on a machine where someone uses NVDA.
- **Self-hosted Linux runners:** Orca allows only one instance per Linux user, so do not run concurrent jobs or a desktop Orca under the same user.
- **Other CI systems (GitLab, Azure Pipelines, and others):** untested, though the same runner requirements apply. Install `asc` from a GitHub release:
  - Windows: download the MSI and `SHA256SUMS`, verify the SHA-256, run `msiexec /i FILE.msi /qn`, add `%LOCALAPPDATA%\Programs\agent-speech-check` to `PATH`, and run `asc get-nvda`.
  - Linux: install Orca 51+ and the packages in the Linux example, run `install.sh` (`ASC_VERSION=X.Y.Z` pins a release), and add `~/.local/bin` to `PATH`.
  - If the CI system does not set `CI`, run `asc config set auto_update false`.

## Auto-update

An installed `asc` skips [auto-update](cli.md#updates) whenever the `CI` environment variable is set (`CI=false`, `0`, or empty count as unset). GitHub Actions and GitLab set `CI`; on systems that do not, run `asc config set auto_update false`.

## Speech in logs

Command output (`asc press`, `asc speech`, and failed `--expect` or `asc wait`) contains what the screen reader said, and job logs of a public repository are public. Do not drive screens that show real secrets.
