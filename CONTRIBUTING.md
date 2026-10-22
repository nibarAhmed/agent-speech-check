# Contributing

Contributions come in under the project's MIT license. There's no CLA.

## Setup

You need Windows: `asc` and its tests drive Windows APIs, so they don't run on macOS or Linux.

1. Fork the repository and clone your fork.
2. Install [uv](https://docs.astral.sh/uv/).
3. `uv sync`

Then, to check your work:

```shell
uv run poe check     # everything: dashes, spelling, format, lint, types, dead code, deps, tests
uv run poe fast      # the quick loop: everything but deps and the slow tests, all failures at once
```

Loop on `poe fast` while you work. `poe check` must pass before you open a pull request.

### End-to-end tests

For changes to the protocol or the NVDA config, also run the end-to-end tests against a real
NVDA: `uv run python scripts/e2e_sandbox.py`. They run inside Windows Sandbox (Windows 11 24H2
or later, Pro, Enterprise or Education), so your own NVDA is never touched. If you can't run
them, say so in the pull request.

Never set `ASC_E2E_NVDA` on your own machine: those tests start that NVDA, which replaces your
running one, and nothing starts yours again. Without it, `pytest -m e2e` skips them. The
sandbox-mode tests (`ASC_E2E_SANDBOX=1`) run on your machine and leave your NVDA alone.

### The installer

You need this only when you change `installer/` or debug CI's `installer` check.
`uv run python scripts/build_installer.py` builds the MSI into `dist/`. It needs the
[.NET SDK](https://dotnet.microsoft.com/download), for WiX, which `dotnet-tools.json` pins.
CI's `installer` check builds it too, then installs, upgrades and uninstalls it.

## Where things are

- [docs/cli.md](docs/cli.md): every command and setting.
- [docs/protocol.md](docs/protocol.md): how `asc` talks to NVDA, with the evidence.
- [docs/findings.md](docs/findings.md): how Windows and NVDA behaved around it.

## Pull requests

- Push a branch to your fork and open a pull request against `main`.
- Pull requests are squash-merged, and the pull request's title becomes the commit on `main`.
  Write the title as a conventional commit, like `fix(setup): say where the settings were saved`.
  Keep pull requests small.
- CI must pass. The `e2e` check skips PRs that change only docs. It runs the real-NVDA tests on
  a throwaway runner, but GitHub's runners have no Windows Sandbox, so run the sandbox tests
  yourself when they apply (see End-to-end tests).
- Your branch must be up to date with `main`. When `main` moves, use "Update branch" and let CI
  run again.
- Resolve every review thread before merging.

## Rules

The full testing policy, code rules and writing rules are in `CLAUDE.md`. It's written for coding
agents, but the Never, Testing policy, Code, Security promises and Writing sections apply to people
too. The ones people trip on most:

- Tests first for new logic, a failing test first for a bug, no mocks of our own code, and never
  weaken a test to get green.
- No em dashes (U+2014) anywhere. `scripts/check_dashes.py` enforces it.
- Never copy NVDA source code (it's GPL). Work from `docs/protocol.md`.
- Update `docs/cli.md` and `skills/asc/SKILL.md` with every command change.
- A new dependency is fine when it saves code. Say why in the pull request description.

## Tests next to a real `asc`

Some tests touch machine-wide state: window titles, the foreground window, global hotkeys and
Windows Sandbox. If you also use `asc` for real on the same machine (another project's sandbox
or daemon), those tests can meet it and fail. Check for a running sandbox or `asc` daemon before
blaming your change, and don't stop processes you didn't start. When a test clashes, give it its
own names, titles and hotkeys instead of skipping it: skips can push coverage under the 90% floor.
Tests never use the default panic key (they set `panic_key`), and each test that registers a
hotkey uses one no other test uses (Control+Shift+Alt with F21, F22 or F23), because test
workers run in parallel.

## Issues

Use the bug report or feature request form. Report security problems privately, as
[SECURITY.md](SECURITY.md) explains, never in an issue.
