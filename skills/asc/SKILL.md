---
name: asc
description: Check what the NVDA screen reader actually says, or drive an app with NVDA keys, using asc (Agent Speech Check). Use when verifying screen reader output, live regions, focus order, labels, or NVDA keyboard navigation in a Windows or web app.
---

# asc: hear what NVDA says

`asc` connects to NVDA over NVDA Remote Access and records every utterance as
numbered lines. If `asc` isn't on PATH, the project's CLAUDE.md says how to run
it. `asc --help` and `asc <command> --help` are short and exact; trust them over
this file.

## Pick the mode

| Need | Mode | Notes |
|---|---|---|
| Hear what the user's own NVDA says while you act some other way (browser tools, tests, the user) | `watch` (default) | Never sends keys. The user connects their NVDA once (below). |
| Press NVDA keys yourself | `sandbox` | NVDA runs in Windows Sandbox, so keys never reach the user's desktop. |
| Press keys in the user's real session | `shared` | Only when the user explicitly asks, in this conversation. Never on your own. |

- `desktop` mode refuses while any `nvda.exe` runs. If the user runs NVDA as
  their screen reader, it never works on their machine; don't try to stop their
  NVDA to make it work.
- Never run an NVDA binary or installer on the host yourself: a second NVDA
  restarts the user's screen reader.

## Workflow

```
asc start                 # prints status; exit 4 if already running (fine)
asc mark                  # everything after this counts
...act (click, navigate, run the test)...
asc wait "(?i)saved"      # returns as soon as a line matches, else exit 1
asc speech                # all lines since the mark
asc stop                  # when done
```

- `wait` takes a Python regex, searched, `(?i)` for case. Default timeout 5000
  ms (`--timeout`). On timeout it prints what NVDA did say: read it before
  retrying.
- Mark before each step, so old speech can't satisfy the next `wait`.
- `asc status` shows the connection, the browse/focus mode (`unknown` until NVDA
  first switches) and the last error.

## Watch mode: first connection

`asc start` prints a host, a key and a certificate fingerprint. Ask the user to
open NVDA's Tools menu, Remote Access, Connect, choose "allow this computer to be
controlled", enter that host and key, and trust the certificate. After that their
NVDA reconnects by itself while `asc` runs. Until it connects, `wait` and
`speech` exit 3. Watch mode records everything NVDA says, typed text included,
so `asc stop` when you're done.

## Pressing keys (sandbox or shared)

```
asc press Tab
asc press NVDA+Tab --expect "(?i)button"
asc press H H "Control+Home"
asc type "hello"
asc launch "C:\share\app\run.cmd" --title "My App"
```

- Gestures are NVDA's names joined by `+`: `NVDA+T`, `Control+Home`,
  `NVDA+Shift+Up`. Every gesture is checked before any is sent; an unknown key
  exits 2 with suggestions.
- Each press waits for quiet (`--settle`, default 400 ms) and prints only the new
  speech. `--expect REGEX` exits 1 unless it matched.
- Look commands up instead of guessing: `asc keys heading`, `asc keys report
  title`. In browse mode single letters jump (`h` heading, `k` link, `f` form
  field); add Shift to go back.
- Sandbox mode: one sandbox at a time, and `asc start` takes about a minute. The
  app must come from a folder listed in the target's `share`
  (`targets.toml`), seen inside as `C:\share\<folder name>`. The sandbox has no
  Edge and no Notepad. `asc sandbox-setup` must have been run once (exit 3 says
  so).
- Shared mode needs `asc start --mode shared --i-understand` every time. Tell
  the user the panic key, Control+Shift+Alt+F12, before sending anything.

## Exit codes

0 ok, 1 wait or `--expect` failed, 2 usage, 3 not connected or not running, 4
refused, 5 protocol error, 6 bug in asc (the message says where the log is).
On 3, run `asc status`; it says how to connect.
