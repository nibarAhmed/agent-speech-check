# Contributing

Contributions come in under the project's MIT license. There's no CLA.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync`
3. `uv run poe check` must pass before you open a pull request.

## Rules

- Testing policy, code rules and writing rules are in `CLAUDE.md`; they apply to people too.
- In short: tests first for new logic, a failing test first for a bug, no mocks of our own code,
  and never weaken a test to get green.
- No em dashes (U+2014) anywhere. `scripts/check_dashes.py` enforces it.
- Never copy NVDA source code (it's GPL). Work from `docs/protocol.md`.
- Conventional commits, small pull requests, and update `docs/cli.md` with every command change.
