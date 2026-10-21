# Agent Speech Check (`asc`)

**Let AI agents test your app with a real NVDA screen reader.**

Agents can inspect a page's HTML and accessibility tree, but that does not tell them exactly what a screen reader announces as someone navigates. `asc` connects to NVDA, lets a test press keys, captures NVDA's speech as text, and checks that speech against expectations.

Use it from a terminal, an AI coding agent, or CI on Windows.

## Example

```shell
asc start --mode desktop
asc launch "C:\path\to\your-app.exe"
asc press Tab
asc press Shift+Tab --expect "expected accessible name"
asc stop
```

Replace the app path and expected speech with values from your application. `asc press` prints what NVDA said; `--expect` makes the command fail when the expected text is not present. See the [CLI reference](docs/cli.md) for exact behavior and options.

## Requirements

- Windows 11.
- NVDA 2026.2 or later. Earlier versions with Remote Access built in (2025.1 onwards) send no speech when there is no audio device ([details](docs/findings.md#nvda-20251)).
- For sandbox mode: Windows 11 24H2 or later (for the `wsb` command), Pro, Enterprise, or Education, with Windows Sandbox turned on, plus the one-time `asc sandbox-setup` step.

## Choose a mode

| Mode | Best for | What it does |
|---|---|---|
| `sandbox` | Isolated tests | Runs NVDA inside Windows Sandbox so test input stays away from your normal desktop. Requires a supported Windows edition and one-time setup. |
| `desktop` | Local development and CI | Starts a portable NVDA with a private configuration. It will not start while another `nvda.exe` is running. Its keys go to your desktop, so it provides a panic hotkey. |
| `watch` | Listening to an NVDA you already run | Captures speech from your existing NVDA. `asc` does not send keys in this mode. |
| `shared` | Driving an existing NVDA session | Sends keys to your real Windows session. Requires an explicit `--i-understand` on each start and provides a panic hotkey. Use with care. |

If you are unsure, prefer **sandbox** for isolation or **desktop** when you want a simpler local setup. Read the mode-specific restrictions in the [CLI reference](docs/cli.md#modes) before automating tests.

## Install

With Python and [`uv`](https://docs.astral.sh/uv/) installed, run this from the root of a clone of this repository:

```shell
uv tool install .
```

If `asc` is not found, check that `uv`'s tool bin directory is on `PATH`, then open a new terminal.

To uninstall, run `asc stop`, then `uv tool uninstall agent-speech-check`, and delete `%LOCALAPPDATA%\agent-speech-check` (settings, logs, relay keys and the portable NVDA). The MSI installer does all of this itself ([details](docs/cli.md#files)).

## Get started

1. Run `asc setup` to pick a mode and create the configuration. It offers to download NVDA for desktop mode and to add the firewall rule sandbox mode needs (that step asks for admin). You can change settings later with `asc config`.
2. Start a session, launch your app, and press keys, as in the [Example](#example). Use `asc speech --since 0` to see everything NVDA said so far. The first sandbox start takes about a minute.
3. Run `asc stop` when you're done.

Use `asc status` to check the connection and `asc COMMAND --help` for a command's options.

## AI agent skill

The repository includes an [agent skill](skills/asc/SKILL.md) for Claude Code and Codex, so the agent knows how to start a session, inspect speech, handle failures, and stop cleanly.

To install it manually for all your projects, copy the `skills/asc` folder to:

- Claude Code: `~/.claude/skills/`
- Codex: `~/.agents/skills/`

Copy it again after it changes.

## Development

You need Windows: `asc` and its tests drive Windows APIs. With [`uv`](https://docs.astral.sh/uv/) installed. In a clone of this repository:

```shell
uv sync
uv run poe check     # everything: dashes, spelling, format, lint, types, dead code, deps, tests
uv run poe fast      # the quick loop: everything but deps and the slow tests
```

- **End-to-end tests** run NVDA inside Windows Sandbox, so your own NVDA is never touched: `uv run python scripts/e2e_sandbox.py`. Never set `ASC_E2E_NVDA` on your own machine: those tests start that NVDA, which replaces your running one, and nothing starts yours again. Without it, `pytest -m e2e` skips them. CI sets it on a throwaway runner and runs the real-NVDA tests on every PR that changes more than docs, and weekly against the latest NVDA (`.github/workflows/e2e.yml`). The sandbox-mode tests (`ASC_E2E_SANDBOX=1`) run on the host and leave your NVDA alone.
- **The installer:** `uv run python scripts/build_installer.py` builds the MSI into `dist/`. It needs the [.NET SDK](https://dotnet.microsoft.com/download), for WiX, which `dotnet-tools.json` pins.

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests and the rules.

## Security and safety

`asc` handles speech that can include passwords and private messages, and it can press keys. [SECURITY.md](SECURITY.md) lists what it promises and how to report a problem privately.

- `shared` mode, and `desktop` mode when it launches its NVDA on your computer, send keys into your real Windows session. Their panic hotkey, `Control+Shift+Alt+F12` unless the `panic_key` setting changes it, releases held keys and stops `asc` at once.
- The relay certificate and channel key are stored in each mode's folder. Keep that folder private.
- Speech is kept in memory only and never written to a log. Command output can contain it, so treat that output as sensitive if your app shows private data.

## Documentation

- [CLI reference](docs/cli.md): commands, modes, configuration, exit codes, and failure behavior.
- [NVDA Remote Access protocol](docs/protocol.md): observed wire behavior, errors, security, and version compatibility.
- [Findings](docs/findings.md): how Windows Sandbox, Windows' focus rules and NVDA versions behaved in experiments.
- [Agent skill](skills/asc/SKILL.md): the recommended workflow for coding agents.

## License

MIT (see [LICENSE](LICENSE)), except one test fixture copied from NVDA, which is GPL-2: see [tests/fixtures/README.md](tests/fixtures/README.md). NVDA is a trademark of NV Access. This project is not affiliated with NV Access.
