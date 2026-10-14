# Agent Speech Check

Let AI agents test your app with a real NVDA.

AI coding agents can write accessible software but can't hear it. `asc` lets an
agent, a person or CI use the NVDA screen reader the way a blind user does: press
keys, get NVDA's speech back as text, and check it. It works with any Windows app.

`asc` connects to NVDA as a Remote Access client (NVDA 2026.2 or newer), so
nothing is installed inside NVDA.

**Status: all four modes work (tested against NVDA 2026.2).** See
[docs/cli.md](docs/cli.md) for the commands and
[docs/protocol.md](docs/protocol.md) for how `asc` talks to NVDA.

## Requirements

- Windows (tested on Windows 11).
- [uv](https://docs.astral.sh/uv/), which gets Python 3.12 or newer for you.
- NVDA 2026.2 or newer. `asc get-nvda` downloads a portable copy for desktop
  mode, and sandbox mode fetches its own.
- Sandbox mode also needs Windows 11 24H2 or later (for the `wsb` command), Pro,
  Enterprise or Education, with Windows Sandbox turned on, and `asc sandbox-setup`
  run once.

## Modes

- **watch**: listen to your own NVDA while you drive. Never sends keys.
- **desktop**: `asc` runs its own silent portable NVDA on the desktop.
- **sandbox**: `asc` runs its own NVDA inside Windows Sandbox, so your NVDA is never disturbed.
- **shared**: drive your own NVDA, only with an explicit opt-in and a panic hotkey.

## Getting started

Install `asc` one of two ways:

- **Installer (no Python needed, works offline):** run
  `agent-speech-check-VERSION.msi`. It installs for you only, without admin, to
  `%LOCALAPPDATA%\Programs\agent-speech-check` and puts that folder on your PATH.
  Unattended: `msiexec /i agent-speech-check-VERSION.msi /qn`. Uninstall from Installed
  apps, or `msiexec /x agent-speech-check-VERSION.msi /qn`. Your settings in
  `%LOCALAPPDATA%\agent-speech-check` are kept.
- **From source:** install [uv](https://docs.astral.sh/uv/), then in a clone of this
  repository run `uv tool install .` (`uv tool install --reinstall .` to update).

Notes:

- Run `asc stop` before you upgrade or uninstall, so no `asc` is running.
- Open a new terminal afterwards. Terminals and agents already running don't see the new
  PATH.
- Install one way only. With both, the one earlier on PATH wins.
- `asc start` still downloads NVDA on first use.
- Uninstalling doesn't remove the firewall rule `asc sandbox-setup` adds, since that rule
  needed admin. Remove it as shown under
  [`asc sandbox-setup`](docs/cli.md#asc-sandbox-setup---target-name).

```
asc setup      # a few questions; saves your settings
asc start
asc status
```

`asc config` changes one setting at a time. See [docs/cli.md](docs/cli.md).

## Claude Code skill

[skills/asc/SKILL.md](skills/asc/SKILL.md) tells an agent when to use `asc`,
the workflow and the gotchas. Install it for all your projects by copying the `skills/asc` folder to
`~/.claude/skills/`, and copy it again after it changes.

## Development

```
uv sync
uv run poe check
```

`uv run python scripts/build_installer.py` builds the MSI into `dist/`. It needs the
[.NET SDK](https://dotnet.microsoft.com/download), for WiX, which `dotnet-tools.json` pins.
Running it accepts the WiX 7
[Open Source Maintenance Fee EULA](https://wixtoolset.org/osmf/) for you: organizations with
over $10,000 a year in revenue owe the fee.

See [CONTRIBUTING.md](CONTRIBUTING.md). To report a security problem, see
[SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE). One test fixture, a copy of NVDA's Commands Quick
Reference, is GPL-2 from NV Access; see
[tests/fixtures/README.md](tests/fixtures/README.md). It isn't part of the
installed package.

NVDA is a trademark of NV Access; this project is not affiliated with NV Access.
