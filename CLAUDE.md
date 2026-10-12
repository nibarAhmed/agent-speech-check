# Agent Speech Check (`asc`)

A Python CLI that lets agents use NVDA as a blind user does, by acting as an NVDA
Remote Access client. Commands are in `docs/cli.md`; how it talks to NVDA, with the
evidence, is in `docs/protocol.md`.

## Commands

- `uv sync`
- `uv run poe check` (everything: dashes, spelling, format, lint, types, dead code, deps, tests)
- `uv run pytest tests/unit -q`
- `uv run pytest -m e2e` (needs a portable NVDA)

## Testing policy

- You're not done until `uv run poe check` passes. Paste the command and its output as evidence.
- For a bug: first write a test that reproduces it and see it fail, then fix it.
- For new logic (parsing, waits, protocol): tests first.
- Never weaken, skip, delete or `xfail` a test to get green. If a test seems wrong, say why and ask.
- No mocks of our own code. The one fake sits at the system boundary: `tests/fake_nvda.py`, which
  speaks the real protocol over real TLS. Change it only together with `docs/protocol.md`, citing
  evidence from real NVDA.
- For changes that touch the protocol or NVDA config, run `pytest -m e2e` against real NVDA, or
  state clearly that it wasn't run.

## Code

- Runtime stays stdlib-only. Any new dependency needs a stated reason.
- No `print` outside `cli.py`.
- Never copy or import NVDA source (GPL). Implement from `docs/protocol.md`.
- The rule that watch mode refuses keys lives in the daemon.
- Raise only `AscError` subclasses from `errors.py` for expected failures (bad input, daemon down, NVDA
  unreachable). Exit codes are decided there and nowhere else. Don't wrap bugs or broken invariants:
  let them crash, and `cli.main` reports them as exit 6.
- Catch broad exceptions only in `cli.main` and the daemon's request handler.
- Validate at the edge into frozen dataclasses, then trust them inside. Check all input before any
  side effect.
- Key sending goes through the one locked function that guarantees release.

## Writing

- No em dashes (U+2014) anywhere: code, comments, docs, commits, help text. Use a colon, comma,
  parentheses or a new sentence.
- CLI output and help are plain and short, because agents pay per token.

## Etiquette

- Small PRs. Conventional commits.
- Update `docs/cli.md` with every command change.
