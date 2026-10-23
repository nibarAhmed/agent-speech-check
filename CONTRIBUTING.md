# Contributing

Contributions come in under the project's MIT license. There's no CLA.

## Setup

1. Fork the repository and clone your fork.
2. Set it up as [Development](README.md#development) in the README explains.
3. `uv run poe check` must pass before you open a pull request. While you work,
   `uv run poe fast` is the quicker loop.
4. For changes to the protocol or the NVDA config, also run the end-to-end tests against a real
   NVDA: `uv run python scripts/e2e_sandbox.py`. They need Windows Sandbox (Windows 11 24H2 or
   later, Pro, Enterprise or Education). If you can't run them, say so in the pull request.
5. Build the installer only when you change `installer/` or debug CI's `installer` check, which
   builds, installs, upgrades and uninstalls the MSI.

## Documentation

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
  yourself when they apply (Setup, step 4).
- Your branch must be up to date with `main`. When `main` moves, use "Update branch" and let CI
  run again.
- Resolve every review thread before merging.

## Rules

Some of the most important rules (the full set is in [CLAUDE.md](CLAUDE.md)):

- Tests first for new logic, a failing test first for a bug, no mocks of our own code, and never
  weaken a test to get green.
- No em dashes (U+2014) anywhere.
- Never copy NVDA source code (it's GPL). Work from `docs/protocol.md`.
- Update `docs/cli.md` and `plugins/agent-speech-check/skills/asc/SKILL.md` with every command change.
- A new dependency is fine when it saves code. Say why in the pull request description.

## Releasing

Maintainers release by pushing a tag. `.github/workflows/release.yml` then builds the MSI, the
wheel, the sdist and the winget manifests, attaches them to a GitHub Release, and uploads the
wheel and sdist to PyPI.

1. In a pull request, set the new version in `pyproject.toml` and in both
   `plugins/agent-speech-check/.claude-plugin/plugin.json` and
   `plugins/agent-speech-check/.codex-plugin/plugin.json` (a test checks they match), then run `uv lock`.
   Plugin users get the new skill only when this version changes.
2. Once it's merged, tag that commit on `main` and push the tag:
   `git tag -a v1.2.3 -m "v1.2.3"`, then `git push origin v1.2.3`. The workflow refuses a tag
   that isn't `v` plus the version in `pyproject.toml`, or that isn't on `main`.
3. Once the release is out, submit it to winget: download its three
   `nibarAhmed.AgentSpeechCheck*.yaml` files into an empty folder and run
   `wingetcreate submit FOLDER`. Store a GitHub token first with `wingetcreate token --store`,
   which signs you in through the browser, so the token stays out of your shell history. That
   opens a pull request on `microsoft/winget-pkgs`. CI's `installer` check already installs,
   lists and uninstalls the MSI with winget from these manifests.

A PyPI version can never be uploaded twice, so a broken release gets a new version, not a
second try at the same one.

Once, before the first release: on pypi.org, add a pending Trusted Publisher for the project
`agent-speech-check`, with owner `nibarAhmed`, repository `agent-speech-check`, workflow
`release.yml` and environment `pypi`. The workflow then publishes with no stored token.

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
