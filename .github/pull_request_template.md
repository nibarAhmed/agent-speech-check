<!--
The title becomes the commit on main: a conventional commit in plain words, like
"fix(setup): say where the settings were saved". The description becomes its message, so
replace each comment with your text.
-->

## Problem

<!-- What was wrong or missing, in a sentence or two. For a bug, how to reproduce it. -->

## Change

<!-- How this fixes it, not a list of changed files. Say why for a new dependency, and who must
change what for a breaking change. -->

## Verification

<!-- What you ran and what you saw. "Tests pass" alone isn't evidence.
- `uv run poe check`: its final summary lines.
- A changed command: a short `uv run asc` transcript, before and after. AGENTS.md (Testing
  policy) says how to run it safely.
- Protocol or NVDA config changes: the result of `uv run python scripts/e2e_sandbox.py`, or why
  it wasn't run.
- Anything you couldn't check. -->

<!-- If an agent did the work, end with: Created with <model> in <harness>. -->
