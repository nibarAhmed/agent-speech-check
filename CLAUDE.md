# Agent Speech Check (`asc`)

A Python CLI that lets agents use NVDA as a blind user does, by acting as an NVDA
Remote Access client. Commands are in `docs/cli.md`; how it talks to NVDA, with the
evidence, is in `docs/protocol.md`; how Windows and NVDA behaved around it is in
`docs/findings.md`. Windows only: the code and its tests drive Windows APIs.

## Never

These can break the user's own machine or the project, so no task overrides them.

- Never set `ASC_E2E_NVDA` on the host: those e2e tests start that NVDA, which replaces the
  user's running one, and nothing starts it again. Use `scripts/e2e_sandbox.py`. The
  sandbox-mode tests (`ASC_E2E_SANDBOX=1`) are made for the host and leave it alone. The
  one exception is a throwaway CI runner, where `.github/workflows/e2e.yml` sets it.
- Never write the user's own NVDA configuration. `asc` writes only its own copies.
- Never stop a process by name (`taskkill /im`, `psutil.process_iter`). Find processes by name if
  you must, but stop only a PID that `asc` or the test started. Inside Windows Sandbox or on a
  throwaway CI runner nothing is the user's, so stopping by name is fine there.
- Never copy or import NVDA source (GPL). Implement from `docs/protocol.md`.
- Never push to `main`: it's protected. Work on a branch and open a PR.

## Commands

- `uv sync`
- `uv run poe check` (everything: spelling, format, lint, types, dead code, deps, tests)
- `uv run poe fast` (the quick loop: everything but deps and the slow tests, all failures at once)
- `uv run python scripts/e2e_sandbox.py` (e2e against a real NVDA in Windows Sandbox)

## Testing policy

- You're not done until `uv run poe check` passes. Paste the command and its output as evidence.
- For a bug: first write a test that reproduces it and see it fail, then fix it.
- For new logic (parsing, waits, protocol): tests first.
- Wait for a condition, never a fixed time: a sleep that guesses how long something takes fails
  on a slow CI runner. Poll what you need (`wait_until`, `Env.wait_running`), with a timeout.
  Sleeping is fine only when the sleep is the behavior under test, like a peer that hangs.
- Never weaken, skip, delete or `xfail` a test to get green. If a test seems wrong, say why and ask.
- No mocks of our own code. The one fake sits at the system boundary: `tests/fake_nvda.py`, which
  speaks the real protocol over real TLS. Change it only together with `docs/protocol.md`, citing
  evidence from real NVDA.
- Code that drives Windows Sandbox, the NVDA installer or a real NVDA is tested by e2e, not by new
  fakes. Its low coverage outside e2e is expected; never lower the 90% coverage floor for it.
- For changes that touch the protocol or NVDA config, run the e2e tests against real NVDA
  (`scripts/e2e_sandbox.py`), or state clearly that they weren't run. The e2e CI workflow also
  runs the real-NVDA tests on PRs, but not the sandbox-mode ones.
- Some tests touch machine-wide state (window titles, the foreground window, global hotkeys,
  Windows Sandbox), so a real `asc` sandbox or daemon on this machine can make them fail. Check for
  one before blaming your change. When a test clashes, give it its own names, titles and hotkeys
  instead of skipping it. Tests never use the default panic key, and each test that registers a
  hotkey uses its own (Control+Shift+Alt with F21, F22 or F23), because workers run in parallel.

## Code

- Layout: `cli.py`, `errors.py` and `validate.py` at the top, the rest in one folder per area. Put
  a new module in its area, and keep the folders' `__init__.py` files empty of imports.
- Runtime dependencies: `cryptography` (the stdlib can't create the relay cert), `tomlkit`
  (the stdlib can read TOML but not write it), `psutil` (finding, waiting for and ending
  processes) and `configobj` (`nvda.ini`, read and written by the same library NVDA uses). Add
  a library when it saves code; say why in the PR description.
- The rule that watch mode refuses keys lives in the daemon.
- Raise only `AscError` subclasses from `errors.py` for expected failures (bad input, daemon down, NVDA
  unreachable). Exit codes are decided there and nowhere else. Don't wrap bugs or broken invariants:
  let them crash, and `cli.main` reports them as exit 6.
- Pick an error's category by the caller's next step, which each category's docstring in
  `errors.py` states, not by what broke.
- Catch broad exceptions only in `cli.main` and the daemon's request handler.
- `AscError` classes never also subclass a built-in exception: the existing `except ValueError`,
  `except OSError` and similar handlers would then catch them by accident.
- Turn stdlib and third-party exceptions into `AscError`s where the code touches the outside world
  (`raise X from None`). Built-ins may stay inside low-level modules like `winapi.py`.
- An error from the daemon reaches the CLI as its bare category (`errors.from_kind`). Around
  `ipc.call`, catch a concrete class only if the client raises it itself (like `NotRunning`).
- Validate at the edge into frozen dataclasses, then trust them inside. Check all input before any
  side effect.
- Keys are sent only from `keyboard.py`. Presses go through the one locked function that guarantees
  release; `Keyboard` alone tracks which keys may be held.
- A one-way door is a bug: whatever `asc` starts, holds or changes, it can also stop, release or
  undo, including after a crash or a timeout.

## Security promises

`SECURITY.md` makes these promises to users. Code and reviews must keep them.

- Speech stays in memory: `asc` never writes it to a file. Logs never contain speech text or
  tokens: log a length or a type, never the text. Command output and error messages may show
  speech (`NoMatch` lists what NVDA said), because they go to the caller, not to a log.
- The daemon's control port listens on 127.0.0.1 only and needs its random token.
- Watch mode never sends keys.
- `asc` never writes the user's own NVDA configuration (also under Never, above).

## Writing

- No em dashes (U+2014) anywhere: code, comments, docs, commits, help text. Use a colon, comma,
  parentheses or a new sentence.
- CLI output and help are plain and short, because agents pay per token.

## Etiquette

- PRs are squash-merged: the PR title becomes the commit on `main` and the PR description its
  message. Write the title as a conventional commit. Small PRs.
- Update `docs/cli.md` and `skills/asc/SKILL.md` with every command change.
