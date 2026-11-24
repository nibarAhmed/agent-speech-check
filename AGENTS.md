# Agent Speech Check (`asc`)

A Python CLI that lets agents use NVDA as a blind user does, by acting as an NVDA
Remote Access client. Commands are in `docs/cli.md`; how it talks to NVDA, with the
evidence, is in `docs/protocol.md`; how Windows, NVDA, Orca and ssh behaved around it is in
`docs/findings.md`. Windows first: the code and its tests drive Windows APIs. On Linux, desktop
and watch mode use Orca (`orca/`) instead of NVDA.

## Never

These can break the user's own machine or the project, so no task overrides them or the security
promises below. The rest of this file is defaults: if one fights the task, say so and get a
human's sign-off before breaking it.

- Never set `ASC_E2E_NVDA` on the host: those e2e tests start that NVDA, which replaces the
  user's running one, and nothing starts it again. Real-NVDA e2e tests run on a throwaway CI
  runner (`.github/workflows/e2e.yml`).
- Never write the user's own NVDA configuration. `asc` writes only its own copies.
- Never stop a process by name (`taskkill /im`, `psutil.process_iter`). Find processes by name if
  you must, but stop only a PID that `asc` or the test started. On a throwaway CI runner nothing
  is the user's, so stopping by name is fine there.
- Never copy or import NVDA source (GPL). Implement from `docs/protocol.md`.
- Never push to `main`: it's protected. Work on a branch, and open a PR only when asked.

## Commands

- `uv sync`
- `uv run poe check` (everything: spelling, format, lint, types, dead code, deps, tests)
- `uv run poe fast` (the quick loop: everything but deps and the slow tests, all failures at once)
- `uv run python scripts/e2e_linux.py` (e2e against a real Orca in a Linux container, with Docker)
- `uv run pytest tests/unit/test_relay.py -k NAME` (one test). `tests/unit` is in-process,
  `tests/integration` runs real `asc` and daemon processes against `tests/fake_nvda.py`, and
  `tests/e2e` (marker `e2e`) needs a real NVDA or Orca.

## How it fits together

1. `cli.py` validates the command, then sends it to the instance's daemon: JSON lines over
   127.0.0.1 with a token (`daemon/ipc.py`). `asc start` spawns that daemon
   (`daemon/session.py`), which writes a state file once it listens.
2. The daemon (`daemon/server.py`) owns one mode's run: the in-memory speech log, the `Keyboard`,
   waits, and a `Link` to the screen reader (`inputs/keyboard.py`).
3. On Windows the link is `nvda/relay.py`: `asc` hosts the NVDA Remote Access relay and is the
   leader, and NVDA connects out to it as the follower (`docs/protocol.md` says why). Desktop mode
   launches its own portable NVDA with its own config copy; watch and shared mode ask the
   running NVDA to connect.
4. On Linux the link is `orca/link.py`: it pipes `orca/helper.py` (stdlib only) into `python3`
   locally or over ssh, which runs Orca and talks JSON lines back. Remote mode reaches Windows
   NVDA through an ssh tunnel (`system/ssh.py`).
5. `config/targets.py` turns `config.toml` into a frozen `Target`, the one thing the daemon
   starts from.

## Testing policy

- You're not done until `uv run poe check` passes. Paste the command and its final summary lines
  as evidence. A fix or change to what a command does also shows a short transcript of the real
  command, before and after, run as `uv run asc` (the `asc` on PATH may be the installed release).
  Run it in its own `--instance`, against a test app, never the user's own windows or daemon.
- For a bug: first write a test that reproduces it and see it fail, then fix it.
- For new logic (parsing, waits, protocol): tests first.
- Test what can break, once, at the lowest layer that fails without the change. Extend an existing
  case before adding a new test. Skip what types already guarantee, trivial logic, the exact
  wording of help and messages, and smoke runs that only check nothing crashed.
- Wait for a condition, never a fixed time: a sleep that guesses how long something takes fails
  on a slow CI runner. Poll what you need (`wait_until`, `Env.wait_running`), with a timeout.
  Sleeping is fine only when the sleep is the behavior under test, like a peer that hangs.
- Never weaken, skip, delete, `xfail` or retry a test to get green, or raise its timeout. Fix a
  flaky test. If a test seems wrong, say why and ask.
- No mocks of our own code. The one fake sits at the system boundary: `tests/fake_nvda.py`, which
  speaks the real protocol over real TLS. Change it only together with `docs/protocol.md`, citing
  evidence from real NVDA.
- Code that drives the NVDA installer or a real NVDA is tested by e2e, not by new
  fakes.
- CI (`e2e.yml`) runs the real-NVDA and real-Orca e2e suites on every PR, and accepts optional
  `pytest_args` and `nvda_version` inputs via `workflow_dispatch` for targeted runs on a branch
  (`gh workflow run e2e.yml --ref <branch> -f pytest_args="..."`). Locally, run `e2e_linux.py`
  (passing pytest arguments on, like `-k`) when your change touches Linux/Orca or ssh, and say in
  the handoff which ran: real-NVDA e2e is left to CI. A change that touches none of these runs
  none locally.

  | The change touches | Run |
  |---|---|
  | The relay, the protocol, keys, speech, or Windows modes | CI (`e2e.yml`) |
  | Remote mode or ssh | `e2e_linux.py -k "tunnel or taken_port"` locally, plus CI (`e2e.yml`) for Windows |
  | Orca, or desktop or watch mode on Linux | `e2e_linux.py` (`-k watch` for watch mode) |
- Some tests touch machine-wide state (window titles, the foreground window, global hotkeys),
  so a real `asc` daemon on this machine can make them fail. Check for
  one before blaming your change. When a test clashes, give it its own names, titles and hotkeys
  instead of skipping it. Tests never use the default panic key, and each test that registers a
  hotkey uses its own (Control+Shift+Alt with F21, F22 or F23), because workers run in parallel.
- A test's `asc start` in watch or shared mode must never ask the NVDA that runs on this machine
  to connect: write its `config.toml` through `Env.config`, which sets `connect = false`. Only the
  e2e tests leave it on.

## Code

- Layout: `cli.py`, `errors.py` and `validate.py` at the top, the rest in one folder per area. Put
  a new module in its area, and keep the folders' `__init__.py` files empty of imports.
- Add a runtime dependency when it saves code. Say why in the PR description and in a comment
  beside it in `pyproject.toml`.
- Validate at the edge into frozen dataclasses, then trust them inside. Check all input before any
  side effect.
- Keys are sent only from `keyboard.py`. Presses go through the one locked function that guarantees
  release; `Keyboard` alone tracks which keys may be held.
- A one-way door is a bug: whatever `asc` starts, holds or changes, it can also stop, release or
  undo, including after a crash or a timeout.
- Default to no comment. Write one only for a constraint, an outside quirk or an invariant the code
  can't show, in one line where you can and four at most. Don't restate names, narrate steps,
  record history or explain the fix you just made: the PR carries that. A longer story goes in
  `docs/findings.md`.

### Errors

- Raise only `AscError` subclasses from `errors.py` for expected failures (bad input, daemon down, NVDA
  unreachable). Exit codes are decided there and nowhere else. Don't wrap bugs or broken invariants:
  let them crash, and `cli.main` reports them as exit 4.
- Pick an error's category: `ExpectationFailed` (1) for a failed wait/match, `UsageError` (2) for bad
  input, config or invocation, `OperationalError` (3) for runtime, daemon, setup, policy, I/O or
  protocol failures. Raise the category directly unless code catches a concrete subclass or several
  call sites share its formatting.
- Catch broad exceptions only in `cli.main` and the daemon's request handler.
- `AscError` classes never also subclass a built-in exception: the existing `except ValueError`,
  `except OSError` and similar handlers would then catch them by accident.
- Turn stdlib and third-party exceptions into `AscError`s where the code touches the outside world
  (`raise X from None`). Built-ins may stay inside low-level modules like `winapi.py`.
- An error from the daemon reaches the CLI as its bare category (`errors.from_kind`). Around
  `ipc.call`, catch a concrete class only if the client raises it itself (like `NotRunning`).

## Security promises

`SECURITY.md` makes these promises to users. Code and reviews must keep them.

- Speech stays in memory: `asc` never writes it to a file. Logs never contain speech text or
  tokens: log a length or a type, never the text. Command output and error messages may show
  speech (`NoMatch` lists what NVDA said), because they go to the caller, not to a log.
- The daemon's control port listens on 127.0.0.1 only and needs its random token.
- Watch mode never sends keys; the daemon enforces this.
- Shared mode starts only with `--i-understand`.
- An installed `asc` (the MSI, or the Linux tarball) updates itself only from this repository's
  GitHub releases, over HTTPS, with an MSI or tarball whose SHA-256 matches the one GitHub lists.

## Writing

- No em dashes (U+2014) anywhere: code, comments, docs, commits, help text. Use a colon, comma,
  parentheses or a new sentence.
- CLI output and help are plain and short, because agents pay per token.
- When a documented decision or finding changes, rewrite or remove the old text. Don't append
  another account of the new behavior, and don't document what the code already shows
  (constants, timeouts, field lists): anything that can drift will drift.

## Etiquette

- PRs are squash-merged: the PR title becomes the commit on `main` and the PR description its
  message. Write the title as a conventional commit, in plain words: what changes for the user.
- One request is one PR. Keep it to what the request needs, and list anything extra as a
  follow-up.
- Fill in `.github/pull_request_template.md`, also when you write the body yourself:
  `gh pr create --body` skips the template.
- Credit agents only in the PR's `## Who did the work` section. No `Co-authored-by` trailers
  for models in commits and no "Generated with" footers: GitHub folds commit trailers into the
  squash commit.
- Update `docs/cli.md` and `plugins/agent-speech-check/skills/asc/SKILL.md` with every command change.
- Don't commit plans, research notes or scratch files. Keep them outside the repo.
