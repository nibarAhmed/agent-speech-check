---
name: asc
description: Check what the NVDA screen reader actually says, or drive an app with NVDA keys, using asc (Agent Speech Check). Use when verifying screen reader output, live regions, focus order, labels, or NVDA keyboard navigation in a Windows or web app.
---

# asc: hear what NVDA says

`asc` connects to NVDA over NVDA Remote Access and records every utterance as
numbered lines. If `asc` isn't on PATH, ask the user to install it: in a clone of
the agent-speech-check repository, `uv tool install .`. `asc --help` and
`asc <command> --help` are short and exact; trust them over this file.

## Pick the mode

| Need | Mode |
|---|---|
| Hear the user's own NVDA while you act some other way (browser tools, tests, the user) | `watch` (default, never sends keys) |
| Press keys yourself, and no NVDA runs on this machine | `desktop` (needs `asc get-nvda` once) |
| Press keys yourself while the user's NVDA runs | `sandbox` (NVDA in Windows Sandbox; `asc start` takes about a minute) |
| Press keys in the user's real session | `shared`, only when the user explicitly asks in this conversation |

## Rules

- Never run an NVDA binary or installer on the host yourself: a second NVDA
  restarts the user's screen reader. `asc get-nvda` and `asc start` are safe.
- Desktop mode refuses while any NVDA runs; never stop the user's NVDA.
- Never run `asc setup`: it asks questions and replaces all the user's settings.
  Change one setting with `asc config set NAME VALUE [--mode M]`, and tell the user
  what you changed.
- `asc sandbox-setup` shows an admin prompt: ask the user to run it.
- Shared and desktop mode send keys to the user's desktop. Tell the user the
  panic key from `asc status` before you send any (a `launch = false` mode has none).
- If the user names their own portable NVDA, set it for desktop or sandbox mode
  (`asc config set nvda_dir DIR --mode desktop`); then `asc get-nvda` isn't needed.
  It runs with a copy of their settings; `asc` never writes their copy.
- A mode with `launch = false` (NVDA started elsewhere, which connects by itself)
  starts no NVDA and no sandbox: none of the desktop or sandbox notes here apply,
  and `launch` runs the command on this computer.

## Workflow

```
asc start                         # the mode setting, or --mode M
asc mark                          # mark: 40, before each step
...act (click, navigate, run the test)...
asc wait --since 40 "(?i)saved"
asc speech --since 40
asc stop                          # when done
```

- For `--since`, N from `(nothing said after N)` counts too. Numbers restart
  with each `asc start`.
- If `asc start` says `asc` already runs, another session may own it: stop it
  only if it's the wrong mode.
- If presses keep missing speech or a slow app times out, raise `settle_ms`,
  `timeout_ms` or `launch_timeout_ms` with `asc config set`.

## Watch mode

`asc start` prints a host, a key and a certificate fingerprint. Ask the user to
connect in NVDA: Tools, Remote Access, Connect, allow this computer to be
controlled, that host and key, and trust the certificate. After that it
reconnects by itself whenever watch mode runs. Watch mode records everything
NVDA says, typed text included, so `asc stop` when you're done.

## Apps

- A `launch` that times out but lists a title under `New windows:` started
  anyway. In watch mode the user may need to switch to that window.
- Sandbox mode: your app must come from a folder in sandbox mode's `share`
  (`asc config set share DIR --mode sandbox`), seen inside as `C:\share\<name>`.
- Shared mode needs `asc start --mode shared --i-understand` every time.

## Exit codes

| Code | Meaning | Next step |
|---|---|---|
| 1 | `wait`, `--expect` or `launch` didn't match | read what NVDA did say |
| 2 | usage | fix the arguments or setting |
| 3 | not connected or not running | follow the hint: `asc start` if not running, else `asc status` says how to connect |
| 4 | refused or busy | read the message |
| 5 | protocol error | report it |
| 6 | bug in asc | report it; the message says where the log is |
| 7 | setup needed | tell the user the hint; retrying won't help |
