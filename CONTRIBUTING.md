# Contributing

Contributions come in under the project's MIT license. There's no CLA.

## Setup

You need Windows: `asc` and its tests drive Windows APIs, so they don't run on macOS or Linux.

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync`
3. `uv run poe check` must pass before you open a pull request.
4. For changes to the protocol or the NVDA config, also run the end-to-end tests against a real
   NVDA: `uv run python scripts/e2e_sandbox.py`. They run inside Windows Sandbox (Windows 11
   24H2 or later, Pro, Enterprise or Education), so your own NVDA is never touched. If you
   can't run them, say so in the pull request.

## Pull requests

- Changes reach `main` only through a pull request. Direct pushes to `main` are refused.
- Pull requests are squash-merged, and the pull request's title becomes the commit on `main`.
  Write the title as a conventional commit, like `fix(setup): say where the settings were saved`.
- CI must pass: `check (3.12)`, `check (3.13)`, `installer`, `zizmor` (GitHub Actions security)
  and `pr-title` (the title is a conventional commit). `e2e` must pass too when it runs: it skips PRs that change only docs, so
  it isn't a required check. It runs the real-NVDA tests on a throwaway runner, but not the
  sandbox-mode ones, because GitHub's runners have no Windows Sandbox, so the template asks
  whether you ran `scripts/e2e_sandbox.py`.
- Your branch must be up to date with `main`. When `main` moves, use "Update branch" and let CI
  run again.
- Resolve every review thread before merging.

## Rules

- The full testing policy, code rules and writing rules are in `CLAUDE.md`. It's written for
  coding agents, but the Never, Testing policy, Code, Security promises and Writing sections apply
  to people too.
- In short: tests first for new logic, a failing test first for a bug, no mocks of our own code,
  and never weaken a test to get green.
- No em dashes (U+2014) anywhere. `scripts/check_dashes.py` enforces it.
- Never copy NVDA source code (it's GPL). Work from `docs/protocol.md`.
- Conventional commits, small pull requests, and update `docs/cli.md` and `skills/asc/SKILL.md`
  with every command change.
- A new dependency is fine when it saves code. Say why in the pull request description.

## Issues

Use the bug report or feature request form. Report security problems privately, as
[SECURITY.md](SECURITY.md) explains, never in an issue.

## Tests next to a real `asc`

Some tests touch machine-wide state: window titles, the foreground window, global hotkeys and
Windows Sandbox. If you also use `asc` for real on the same machine (another project's sandbox
or daemon), those tests can meet it and fail. Check for a running sandbox or `asc` daemon before
blaming your change, and don't stop processes you didn't start. When a test clashes, give it its
own names, titles and hotkeys instead of skipping it: skips can push coverage under the 90% floor.
Tests never use the default panic key (they set `panic_key`), and each test that registers a
hotkey uses its own, because test workers run in parallel.
