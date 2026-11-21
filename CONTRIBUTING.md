# Contributing

Contributions come in under the project's MIT license. There's no CLA.

## Setup

1. Fork the repository and clone your fork.
2. Set it up as [Development](README.md#development) in the README explains.
3. `uv run poe check` must pass before you open a pull request. While you work,
   `uv run poe fast` is the quicker loop.
4. CI runs the end-to-end tests. If your change affects how `asc` talks to NVDA or Orca, run the
   matching local e2e tests listed in [AGENTS.md](AGENTS.md#testing-policy):
   - Real NVDA (needs Windows Sandbox on Windows 11 24H2+ Pro, Enterprise, or Education):
     `uv run python scripts/e2e_sandbox.py -k <tests>`
   - Real Orca (needs Docker): `uv run python scripts/e2e_linux.py -k <tests>`

   If you cannot run them locally, say so in the pull request.
5. Build the installer only when you change `installer/` or debug CI's `installer` check, which
   builds, installs, upgrades and uninstalls the MSI.

## Documentation

- [docs/cli.md](docs/cli.md): every command and setting.
- [docs/protocol.md](docs/protocol.md): how `asc` talks to NVDA, with the evidence.
- [docs/findings.md](docs/findings.md): how Windows, NVDA, Orca and ssh behaved around it.

## Pull requests

- Push a branch to your fork and open a pull request against `main`.
- Pull requests are squash-merged, and the pull request's title becomes the commit on `main`.
  Write the title as a conventional commit, like `fix(setup): say where the settings were saved`.
  Fill in the template.
- Solve one problem per pull request. Include every change it needs (code, tests, `docs/cli.md`,
  the skill). Cleanup, refactors and a second fix go in their own pull requests.
- CI must pass. The `e2e` check passes when the real-NVDA tests, the Linux builds and the
  real-Orca tests pass on throwaway runners. GitHub's runners have no Windows Sandbox, so run
  the sandbox tests yourself when they apply (Setup, step 4).
- Your branch must be up to date with `main`. When `main` moves, use "Update branch" and let CI
  run again.
- Resolve every review thread before merging.

## Rules

The rules for code, tests and docs are in [AGENTS.md](AGENTS.md). They apply to people too.

## Releasing

Maintainers release by pushing a tag. `.github/workflows/release.yml` then builds the MSI and the
Linux tarballs for x86 and Arm (on glibc 2.28, so they run on that and newer) and attaches only
those to a GitHub Release. The winget manifests it writes stay on the workflow run. An installed `asc` updates itself from that release,
so its assets keep their names: `agent-speech-check-VERSION.msi` and
`agent-speech-check-linux-MACHINE.tar.gz`, where MACHINE is `x86_64` or `aarch64`.

1. In a pull request, set the new version in `pyproject.toml` and in both
   `plugins/agent-speech-check/.claude-plugin/plugin.json` and
   `plugins/agent-speech-check/.codex-plugin/plugin.json` (a test checks they match), then run `uv lock`.
   Plugin users get the new skill only when this version changes.
2. Once it's merged, tag that commit on `main` and push the tag:
   `git tag -a v1.2.3 -m "v1.2.3"`, then `git push origin v1.2.3`. The workflow refuses a tag
   that isn't `v` plus the version in `pyproject.toml`, or that isn't on `main`.
3. Once the release is published, submit it to winget (CI's `installer` check already tests
   installing, listing, and uninstalling the MSI from these manifests):
   - **With `wingetcreate`:** First store a GitHub token with `wingetcreate token --store` (it
     signs in through your browser, keeping the token out of shell history). Then download the
     three manifests from the release's workflow run into an empty folder,
     `gh run download RUN_ID -n winget -D FOLDER`, and run `wingetcreate submit FOLDER` to open a
     pull request on `microsoft/winget-pkgs`.
   - **Without `wingetcreate`:** Open a pull request from your fork of `microsoft/winget-pkgs`
     adding those three files under `manifests/n/nibarAhmed/AgentSpeechCheck/1.2.3/`.
   - On your first pull request, sign Microsoft's CLA by commenting
     `@microsoft-github-policy-service agree`.

## Issues

Use the bug report or feature request form. Report security problems privately, as
[SECURITY.md](SECURITY.md) explains, never in an issue.
