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
- Desktop mode refuses while any NVDA runs. Use sandbox mode then; never stop the
  user's NVDA.
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
  it has no panic key, and `launch` runs the command on this computer.

## Workflow

```
asc start                         # the mode setting, or --mode M
asc mark                          # before each step, so old speech can't match
...act (click, navigate, press keys, run the test)...
asc wait "(?i)saved"              # Python regex, searched
asc speech                        # everything since the mark
asc stop                          # when done
```

- If `asc start` exits 4 because `asc` already runs, `asc status` shows which
  mode. Another session may own it: stop it only if it's the wrong mode.
- If `asc start` prints `NVDA <version> is available: asc get-nvda`, it still
  started. To update: `asc stop`, `asc get-nvda`, `asc start` (`get-nvda`
  refuses while desktop mode's NVDA runs).
- If presses keep missing speech or a slow app times out, raise `settle_ms`,
  `timeout_ms` or `launch_timeout_ms` with `asc config set`.

## Watch mode: first connection

`asc start` prints a host, a key and a certificate fingerprint. Ask the user to
open NVDA's Tools menu, Remote Access, Connect, choose "allow this computer to be
controlled", enter that host and key, and trust the certificate. It reconnects by
itself after that. Watch mode records everything NVDA says, typed text included,
so `asc stop` when you're done.

## Apps and keys

```
asc launch "C:\share\app\run.cmd" --title "My App"
asc press Tab
asc press NVDA+Tab --expect "(?i)button"
asc type "hello"
asc keys heading                  # look NVDA commands up instead of guessing
```

- A `launch` that times out but lists a title under `New windows:` started
  anyway. In watch mode the user may need to switch to that window.
- Sandbox mode: commands run inside the sandbox, which has no Edge and no
  Notepad. Your app must come from a folder in sandbox mode's `share`
  (`asc config set share DIR --mode sandbox`), seen inside as `C:\share\<name>`.
- Shared mode needs `asc start --mode shared --i-understand` every time.

## Exit codes

| Code | Meaning | Next step |
|---|---|---|
| 1 | `wait`, `--expect` or `launch` didn't match | read what NVDA did say before retrying |
| 2 | usage | fix the arguments or setting |
| 3 | not connected or not running | follow the hint: `asc start` if not running, else `asc status` says how to connect |
| 4 | refused or busy | read the message |
| 5 | protocol error | report it |
| 6 | bug in asc | report it; the message says where the log is |
| 7 | setup needed | tell the user the hint; retrying won't help |
| 130 | interrupted | |
