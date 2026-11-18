# Agent Speech Check (`asc`)

**Let AI agents test your app with a real NVDA screen reader.**

`asc` connects to NVDA, presses keys, and gives you what NVDA said as text, so you can check it says what you expect. Run it yourself from a terminal, let an AI coding agent drive it, or use it in CI on Windows. On Linux it does the same with Orca.

## Example

```shell
asc get-nvda
asc start --mode desktop
asc launch "C:\path\to\your-app.exe"
asc press Tab
asc press Shift+Tab --expect "expected accessible name"
asc stop
```

`asc get-nvda` downloads the portable NVDA that desktop mode runs, once. Each `asc press` prints what NVDA said, and `--expect` makes it fail when that text is missing. `asc speech --since 0` shows everything NVDA has said so far. Run `asc setup` to pick a default mode.

## Requirements

- Windows 11.
- NVDA 2026.2 or later ([why](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/findings.md#nvda-20251)).
- Sandbox mode also needs Windows 11 24H2 or later (Pro, Enterprise or Education) with Windows Sandbox turned on.

## Choose a mode

| Mode | When to use it |
|---|---|
| `sandbox` | You want NVDA kept away from your desktop. It runs inside Windows Sandbox, and your own NVDA keeps running. |
| `desktop` | You want the simplest setup, or you're in CI. `asc` starts its own portable NVDA, and its keys land on your desktop. It won't start while another NVDA is running. |
| `watch` | You want to hear the NVDA you already run. `asc` only listens and never presses keys. When it connects, NVDA asks you to allow it: answer Yes. |
| `shared` | You want `asc` to press keys in the NVDA you already run, in your real session. You have to pass `--i-understand`. |
| `remote` | The screen reader runs on a machine you manage, like a VM, that `asc` reaches over ssh. On Windows you start NVDA there and it connects to `asc` through an ssh tunnel; on Linux `asc` starts Orca there. One instance per machine: run `asc setup --instance NAME` for each. |
| `orca` | You test with Orca on this Linux machine. `asc` starts its own Orca on a hidden display. It needs Orca 51 or later, Xvfb, openbox, xdotool and python3. |

The [CLI reference](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md#modes) has the details on each mode.

## Install

On Windows, run `agent-speech-check-VERSION.msi` from the [latest release](https://github.com/nibarAhmed/agent-speech-check/releases/latest), then open a new terminal so it picks up the new `PATH`. To uninstall, use Settings > Apps.

On Linux (64-bit x86 or Arm, glibc 2.28 or later), `asc` needs **Orca 51 or later**: check with `orca --version`. Older Orca, like the 50 in Ubuntu 26.04 LTS, lacks the D-Bus service `asc` records speech through, so `asc start` refuses it. Unpack the release into `~/.local/share` and link `asc` into `~/.local/bin`, which most distros put on `PATH`. The same lines also reinstall it:

```shell
mkdir -p ~/.local/share ~/.local/bin
curl -fsSL https://github.com/nibarAhmed/agent-speech-check/releases/latest/download/agent-speech-check-linux-$(uname -m).tar.gz | tar -xz -C ~/.local/share
ln -sf ~/.local/share/agent-speech-check/current/asc ~/.local/bin/asc
```

To uninstall, run `asc stop`, then delete `~/.local/share/agent-speech-check`, `~/.local/bin/asc` and, with your settings and data, `~/.local/state/agent-speech-check`.

Either way, `asc` [keeps itself up to date](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md#updates).

## AI agent skill

The [`asc` skill](https://github.com/nibarAhmed/agent-speech-check/blob/main/plugins/agent-speech-check/skills/asc/SKILL.md) teaches Claude Code and Codex how to use `asc`: which mode fits the task, how to press keys safely, and how to check what NVDA said. Install it once and it works in all your projects:

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
- **The installer:** `uv run python scripts/build_installer.py` builds the MSI into `dist/`. It needs the [.NET SDK](https://dotnet.microsoft.com/download). On Linux, `uv run python -m scripts.build_linux` builds the tarball.

See [CONTRIBUTING.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/CONTRIBUTING.md) for pull requests and the rules.

## Security and safety

Speech can include passwords and private messages, and `asc` can press keys. [SECURITY.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/SECURITY.md) lists what it promises and how to report a problem privately.

- `shared` mode, and `desktop` mode when its NVDA runs on your computer, send keys to your real session. There `Control+Shift+Alt+F12` (the `panic_key` setting) releases held keys and stops `asc` at once.
- Each mode's folder holds its relay certificate and channel key: keep it private.
- Speech is never written to a log, but command output can contain it: treat it as sensitive.

## Documentation

- [CLI reference](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md): commands, modes, configuration, exit codes, and failure behavior.
- [NVDA Remote Access protocol](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/protocol.md): observed wire behavior, errors, security, and version compatibility.
- [Findings](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/findings.md): how Windows Sandbox, Windows' focus rules and NVDA versions behaved in experiments.

## License

MIT (see [LICENSE](https://github.com/nibarAhmed/agent-speech-check/blob/main/LICENSE)), except one test fixture copied from NVDA, which is GPL-2: see [tests/fixtures/README.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/tests/fixtures/README.md). NVDA is a trademark of NV Access. This project is not affiliated with NV Access.
