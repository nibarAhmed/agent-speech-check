# Agent Speech Check (`asc`)

**Let AI agents test your app with a real NVDA screen reader.**

`asc` connects to NVDA, presses keys, captures what NVDA says as text, and checks it against what you expect. Use it from a terminal, an AI coding agent, or CI on Windows.

## Example

```shell
asc start --mode desktop
asc launch "C:\path\to\your-app.exe"
asc press Tab
asc press Shift+Tab --expect "expected accessible name"
asc stop
```

`asc press` prints what NVDA said; `--expect` fails the command when that text is missing.

## Requirements

- Windows 11.
- NVDA 2026.2 or later ([why](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/findings.md#nvda-20251)).
- For sandbox mode: Windows 11 24H2 or later, Pro, Enterprise or Education, with Windows Sandbox turned on.

## Choose a mode

| Mode | Best for | What it does |
|---|---|---|
| `sandbox` | Isolated tests | Runs NVDA inside Windows Sandbox, away from your desktop. |
| `desktop` | Local development and CI | Starts a portable NVDA with its own config. Won't start while another `nvda.exe` runs. Its keys go to your desktop. |
| `watch` | Listening to the NVDA you already run | Captures its speech and never sends keys. On start, your NVDA asks to allow the connection: answer Yes. |
| `shared` | Driving the NVDA you already run | Sends keys to your real session. Needs `--i-understand`. |

Unsure? Use **sandbox**, or **desktop** for a simpler setup. Details are in the [CLI reference](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md#modes).

## Install

- **MSI installer** (no Python needed): run `agent-speech-check-VERSION.msi` from the [latest release](https://github.com/nibarAhmed/agent-speech-check/releases/latest). It installs for your user, without admin.
- **With [`uv`](https://docs.astral.sh/uv/)**, for the latest code on `main`: `uv tool install git+https://github.com/nibarAhmed/agent-speech-check`. Update with `uv tool upgrade agent-speech-check`.

Open a new terminal afterwards so it sees the new `PATH`.

To uninstall the MSI, use Settings > Apps. For `uv`, run `asc stop`, then `uv tool uninstall agent-speech-check`, and delete `%LOCALAPPDATA%\agent-speech-check`.

## Get started

1. Run `asc setup` to pick a mode and write the configuration. Change it later with `asc config`.
2. Start a session, launch your app and press keys, as in the [Example](#example). `asc speech --since 0` shows everything NVDA said.
3. Run `asc stop` when you're done.

`asc status` checks the connection; `asc COMMAND --help` lists a command's options.

## AI agent skill

An [agent skill](https://github.com/nibarAhmed/agent-speech-check/blob/main/plugins/agent-speech-check/skills/asc/SKILL.md) teaches Claude Code and Codex the workflow. Install it for all your projects:

```shell
# Claude Code
claude plugin marketplace add nibarAhmed/agent-speech-check
claude plugin install agent-speech-check@agent-speech-check

# Codex
codex plugin marketplace add nibarAhmed/agent-speech-check
codex plugin add agent-speech-check@agent-speech-check
```

To update, run `claude plugin update agent-speech-check@agent-speech-check`, or in Codex `codex plugin marketplace upgrade`, then `codex plugin add` again.

## Development

On Windows, with [`uv`](https://docs.astral.sh/uv/), in a clone:

```shell
uv sync
uv run poe check     # everything: spelling, format, lint, types, dead code, deps, tests
uv run poe fast      # the quick loop: everything but deps and the slow tests
```

- **End-to-end tests** run NVDA inside Windows Sandbox: `uv run python scripts/e2e_sandbox.py`. Never set `ASC_E2E_NVDA` on your own machine: those tests replace your running NVDA, and nothing starts it again.
- **The installer:** `uv run python scripts/build_installer.py` builds the MSI into `dist/`. It needs the [.NET SDK](https://dotnet.microsoft.com/download).

See [CONTRIBUTING.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/CONTRIBUTING.md) for pull requests and the rules.

## Security and safety

Speech can include passwords and private messages, and `asc` can press keys. [SECURITY.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/SECURITY.md) lists what it promises and how to report a problem privately.

- In `shared` and `desktop` mode, `Control+Shift+Alt+F12` (the `panic_key` setting) releases held keys and stops `asc` at once.
- Each mode's folder holds its relay certificate and channel key: keep it private.
- Speech is never written to a log, but command output can contain it: treat it as sensitive.

## Documentation

- [CLI reference](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md): commands, modes, configuration, exit codes, and failure behavior.
- [NVDA Remote Access protocol](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/protocol.md): observed wire behavior, errors, security, and version compatibility.
- [Findings](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/findings.md): how Windows Sandbox, Windows' focus rules and NVDA versions behaved in experiments.
- [Agent skill](https://github.com/nibarAhmed/agent-speech-check/blob/main/plugins/agent-speech-check/skills/asc/SKILL.md): the recommended workflow for coding agents.

## License

MIT (see [LICENSE](https://github.com/nibarAhmed/agent-speech-check/blob/main/LICENSE)), except one test fixture copied from NVDA, which is GPL-2: see [tests/fixtures/README.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/tests/fixtures/README.md). NVDA is a trademark of NV Access. This project is not affiliated with NV Access.
