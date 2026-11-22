# Agent Speech Check (`asc`)

**Let AI agents test your app with a real screen reader (NVDA on Windows, Orca on Linux).**

`asc` connects to NVDA on Windows or Orca on Linux, presses keys, and gives you what the screen reader said as text, so you can check it says what you expect. Run it yourself from a terminal, let an AI coding agent drive it, or use it in CI on Windows or Linux.

## Example

```shell
asc get-nvda
asc start --mode desktop
asc launch "C:\path\to\your-app.exe"
asc press Tab
asc press Shift+Tab --expect "expected accessible name"
asc stop
```

`asc get-nvda` downloads a portable copy of NVDA for Windows desktop mode (only needed once; on Linux, `asc start` launches Orca on a hidden display by default). Each `asc press` prints what the screen reader said, and `--expect` fails the command if the output does not match. `asc speech --since 0` shows everything said so far. Run `asc setup` to choose a default mode.

## Requirements

- Windows 11, or Linux with Orca 51 or later (see [Install](#install)).
- On Windows, NVDA 2026.2 or later ([why](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/findings.md#nvda-20251)).
- Sandbox mode also needs Windows 11 24H2 or later (Pro, Enterprise or Education) with Windows Sandbox turned on.

## Choose a mode

| Mode | When to use it |
|---|---|
| `sandbox` | You want NVDA isolated from your desktop. It runs inside Windows Sandbox while your own NVDA keeps running undisturbed. |
| `desktop` | You want the simplest setup, or you are running in CI. On Windows, `asc` starts its own portable NVDA and sends keys to your desktop (it refuses to start if another NVDA is already running). On Linux, `asc` starts its own Orca on a hidden virtual display (requires Orca 51+, Xvfb, openbox, xdotool, dbus-daemon, gdbus, and python3). |
| `watch` | You want to record speech from your own running screen reader without letting `asc` press keys. On Windows, NVDA prompts you to allow the connection (answer Yes). On Linux, restart your Orca once with the command `asc start` prints. |
| `shared` | You want `asc` to press keys in the NVDA you already run, inside your real Windows session. Requires `--i-understand` on every `asc start`. |
| `remote` | The screen reader runs on another machine (such as a VM) reached over ssh. With `os = "windows"`, you start NVDA on that machine and it connects back through an ssh tunnel; with `os = "linux"`, `asc` starts Orca there. Uses one named instance per machine (`asc setup --instance NAME` or `asc config set ... --instance NAME`). |

The [CLI reference](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md#modes) has the details on each mode.

## Install

On Windows, run `agent-speech-check-VERSION.msi` from the [latest release](https://github.com/nibarAhmed/agent-speech-check/releases/latest), then open a new terminal so it picks up the new `PATH`. To uninstall, use Settings > Apps.

On Linux (64-bit x86 or Arm, glibc 2.28 or later), `asc` requires **Orca 51 or later** (check with `orca --version`). Older releases, such as Orca 50 in Ubuntu 26.04 LTS, lack the D-Bus service `asc` uses to record speech, so `asc start` refuses them. Unpack the release into `~/.local/share` and symlink `asc` into `~/.local/bin` (which most distros include on `PATH`). Running the same commands again reinstalls `asc`:

```shell
mkdir -p ~/.local/share ~/.local/bin
curl -fsSL https://github.com/nibarAhmed/agent-speech-check/releases/latest/download/agent-speech-check-linux-$(uname -m).tar.gz | tar -xz -C ~/.local/share
ln -sf ~/.local/share/agent-speech-check/current/asc ~/.local/bin/asc
```

To uninstall on Linux, run `asc uninstall` (pass `--keep-data` to keep your settings and data, or `--yes` to skip prompts). If you used watch mode, restart Orca or log out so Orca drops `asc`'s recording secret.

On both platforms, `asc` [keeps itself up to date](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md#updates).

## AI agent skill

The [`asc` skill](https://github.com/nibarAhmed/agent-speech-check/blob/main/plugins/agent-speech-check/skills/asc/SKILL.md) teaches Claude Code and Codex how to use `asc`: which mode fits the task, how to press keys safely, and how to check what the screen reader said. Install it once and it works in all your projects:

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

- **End-to-end tests** run real NVDA on CI (`.github/workflows/e2e.yml`, on every PR or on demand via `workflow_dispatch`) and Orca in a Linux container locally (`uv run python scripts/e2e_linux.py`, with Docker). Never set `ASC_E2E_NVDA` on your own machine: those tests replace your running NVDA, and nothing starts it again.
- **The installer:** `uv run python scripts/build_installer.py` builds the MSI into `dist/`. It needs the [.NET SDK](https://dotnet.microsoft.com/download). On Linux, `uv run python -m scripts.build_linux` builds the tarball.

See [CONTRIBUTING.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/CONTRIBUTING.md) for pull requests and the rules.

## Security and safety

Speech can include passwords and private messages, and `asc` can press keys. [SECURITY.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/SECURITY.md) lists what it promises and how to report a problem privately.

- In `shared` mode and Windows `desktop` mode (when `asc` launches NVDA), keys go to your real desktop. Press `Control+Shift+Alt+F12` (configurable via `panic_key`) to release held keys and stop `asc` immediately.
- Each mode's folder holds its relay certificate and channel key: keep it private.
- Speech is never written to a log, but command output can contain it: treat it as sensitive.

## Documentation

- [CLI reference](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/cli.md): commands, modes, configuration, exit codes, and failure behavior.
- [NVDA Remote Access protocol](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/protocol.md): observed wire behavior, errors, security, and version compatibility.
- [Findings](https://github.com/nibarAhmed/agent-speech-check/blob/main/docs/findings.md): how Windows Sandbox, Windows' focus rules, NVDA versions, Orca and ssh behaved in experiments.

## License

MIT (see [LICENSE](https://github.com/nibarAhmed/agent-speech-check/blob/main/LICENSE)), except one test fixture copied from NVDA, which is GPL-2: see [tests/fixtures/README.md](https://github.com/nibarAhmed/agent-speech-check/blob/main/tests/fixtures/README.md). NVDA is a trademark of NV Access. This project is not affiliated with NV Access.
