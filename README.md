# Agent Speech Check

Let AI agents test your app with a real NVDA.

AI coding agents can write accessible software but can't hear it. `asc` lets an
agent, a person or CI use the NVDA screen reader the way a blind user does: press
keys, get NVDA's speech back as text, and check it. It works with any Windows app.

`asc` connects to NVDA as a Remote Access client (NVDA 2026.2 or newer), so
nothing is installed inside NVDA. See [docs/cli.md](docs/cli.md) for the
commands and [docs/protocol.md](docs/protocol.md) for how `asc` talks to NVDA.

## Requirements

- Windows (tested on Windows 11).
- NVDA 2026.2 or newer. Watch and shared mode use your own NVDA. `asc get-nvda`
  downloads a portable copy for desktop mode, and sandbox mode fetches its own.
- Sandbox mode also needs Windows 11 24H2 or later (for the `wsb` command), Pro,
  Enterprise or Education, with Windows Sandbox turned on, and `asc sandbox-setup`
  run once.

## Modes

- **watch**: listen to your own NVDA while you drive. Never sends keys.
- **desktop**: asc runs its own silent portable NVDA on the desktop. Only while no other NVDA runs.
- **sandbox**: `asc` runs its own NVDA inside Windows Sandbox, so your NVDA is never disturbed.
- **shared**: drive your own NVDA, only with an explicit opt-in and a panic hotkey.

## Install

Install `asc` one of two ways:

- **Installer (no Python needed, works offline):** there is no release yet, so
  take `agent-speech-check-VERSION.msi` from the `msi` artifact of a
  [CI run](https://github.com/nibarAhmed/agent-speech-check/actions/workflows/ci.yml),
  or build it yourself (see [Development](#development)). Run it: it installs
  for you only, without admin, to `%LOCALAPPDATA%\Programs\agent-speech-check`
  and puts that folder on your PATH. Unattended:
  `msiexec /i agent-speech-check-VERSION.msi /qn`. Uninstall from Installed
  apps, or `msiexec /x agent-speech-check-VERSION.msi /qn`. Your settings in
  `%LOCALAPPDATA%\agent-speech-check` are kept.
- **From source:** install [uv](https://docs.astral.sh/uv/) (it gets Python 3.12
  or newer for you), then in a clone of this repository run `uv tool install .`
  (`uv tool install --reinstall .` to update).

Notes:

- Run `asc stop` before you upgrade or uninstall, so no `asc` is running.
- Open a new terminal afterwards. Terminals and agents already running don't see the new
  PATH.
- Install one way only. With both, the one earlier on PATH wins.
- NVDA isn't included. Desktop mode needs `asc get-nvda` once, and sandbox mode downloads the NVDA installer at its first `asc start`.
- Uninstalling doesn't remove the firewall rule `asc sandbox-setup` adds, since that rule needs admin permissions. Remove it as shown under
  [`asc sandbox-setup`](docs/cli.md#asc-sandbox-setup).

## Getting started

1. Run `asc setup` to set your preferences.
2. Run `asc start` to start `asc` in that mode. In watch mode it prints a host
   and key: connect your NVDA to them once (Tools, Remote Access, Connect).
3. Run `asc status` to check that NVDA is connected.
4. Try it. In desktop, sandbox or shared mode, `asc press Tab` presses Tab and
   prints what NVDA said. In watch mode, use your computer as usual, then run
   `asc speech` to see what NVDA said.
5. Run `asc stop` when you're done.

To change one setting later, use `asc config`. All commands are in
[docs/cli.md](docs/cli.md).

## Agent skill

[skills/asc/SKILL.md](skills/asc/SKILL.md) tells an agent when to use `asc`,
the workflow and the gotchas. It is an [Agent Skill](https://agentskills.io), so
it works in any agent that reads them. To use it in all your projects, copy the
`skills/asc` folder to:

- Claude Code: `~/.claude/skills/`
- Codex: `~/.agents/skills/`

Copy it again after it changes.

## Development

You need Windows: `asc` and its tests drive Windows APIs. With
[uv](https://docs.astral.sh/uv/) installed, in a clone of this repository:

```
uv sync
uv run poe check     # everything: dashes, spelling, format, lint, types, dead code, deps, tests
uv run poe fast      # the quick loop: everything but deps and the slow tests
```

- **End-to-end tests** runs NVDA inside Windows Sandbox, so your own
  NVDA is never touched: `uv run python scripts/e2e_sandbox.py`. Never run
  `pytest -m e2e` directly: it would start a second NVDA.
- **The installer:** `uv run python scripts/build_installer.py` builds the MSI into
  `dist/`. It needs the [.NET SDK](https://dotnet.microsoft.com/download), for WiX,
  which `dotnet-tools.json` pins.

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests and the rules.

## Security

`asc` handles speech that can include passwords and private messages, and it can
press keys. [SECURITY.md](SECURITY.md) lists what it promises and how to report a
problem privately.

## License

MIT, except one test fixture copied from NVDA, which is GPL-2: see
[tests/fixtures/README.md](tests/fixtures/README.md). NVDA is a trademark of NV Access. This project is not affiliated with NV Access.
