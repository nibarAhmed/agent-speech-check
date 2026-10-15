# Contributing

Contributions come in under the project's MIT license. There's no CLA.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync`
3. `uv run poe check` must pass before you open a pull request.
4. For changes to the protocol or the NVDA config, also run the end-to-end tests against a real
   NVDA: `uv run python scripts/e2e_sandbox.py`. They run inside Windows Sandbox (Windows 11
   24H2 or later, Pro, Enterprise or Education), so your own NVDA is never touched. If you can't run them, say so in
   the pull request.

## Rules

- Testing policy, code rules and writing rules are in `CLAUDE.md`; they apply to people too.
- In short: tests first for new logic, a failing test first for a bug, no mocks of our own code,
  and never weaken a test to get green.
- No em dashes (U+2014) anywhere. `scripts/check_dashes.py` enforces it.
- Never copy NVDA source code (it's GPL). Work from `docs/protocol.md`.
- Conventional commits, small pull requests, and update `docs/cli.md` with every command change.
- A new dependency is fine when it saves code. Say why in the commit message.

## Tests next to a real `asc`

Some tests touch machine-wide state: window titles, the foreground window, the panic hotkey and
Windows Sandbox. If you also use `asc` for real on the same machine (another project's sandbox
or daemon), those tests can meet it and fail. Check for a running sandbox or `asc` daemon before
blaming your change, and don't stop processes you didn't start. When a test clashes, give it its
own names and titles instead of skipping it: skips can push coverage under the 90% floor.
