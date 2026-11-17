<!--
The title becomes the commit on main: a conventional commit in plain words, like
"fix(setup): say where the settings were saved". Solve one problem per pull request.
-->

## Problem

<!-- What was wrong or missing, in a sentence or two. For a bug, how to reproduce it. -->

## Change

<!-- How this fixes it, not a list of changed files. Say why for a new dependency, and who must
change what for a breaking change. A changed command also updates docs/cli.md and
plugins/agent-speech-check/skills/asc/SKILL.md. -->

## Verification

<!-- What you ran and what you saw. "Tests pass" alone isn't evidence.
- `uv run poe check`: its final summary line.
- A changed command: a short transcript of the real `asc` command, before and after, run
  against a test app.
- Protocol or NVDA config changes: the result of `uv run python scripts/e2e_sandbox.py`, or why
  it wasn't run.
- Anything you couldn't check. -->

<!-- If an agent did the work, end with: Created with <model> in <harness>. -->
