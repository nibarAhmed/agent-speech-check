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

| Need | Mode | Notes |
|---|---|---|
| Hear what the user's own NVDA says while you act some other way (browser tools, tests, the user) | `watch` (default) | Never sends keys. The user connects their NVDA once (below). |
| Press NVDA keys yourself, and no NVDA is running on this machine | `desktop` | A silent portable NVDA on the desktop. Starts faster than a sandbox. Needs `asc get-nvda` once. |
| Press NVDA keys yourself while the user's NVDA runs | `sandbox` | NVDA runs in Windows Sandbox, so keys never reach the user's desktop. |
| Press keys in the user's real session | `shared` | Only when the user explicitly asks, in this conversation. Never on your own. |

- `desktop` mode refuses (exit 4) while any `nvda.exe` runs. If the user runs
  NVDA as their screen reader, use `sandbox` instead; don't try to stop their
  NVDA to make desktop mode work.
- Never run an NVDA binary or installer on the host yourself: a second NVDA
  restarts the user's screen reader. `asc get-nvda` and `asc start` are safe:
  they never start a second NVDA on the host.
- The user's installed NVDA is used only as it runs: `watch` or `shared`.
- If the user names their own portable NVDA, set it for sandbox or desktop
  mode (`asc config set nvda_dir DIR --mode sandbox`). It runs with a copy of their settings and
  add-ons; `asc` never writes their copy.
- The sandbox window sits in the system tray by default. If the user wants it
  on the taskbar or shown, pass `--window taskbar` or `--window show`.

## Settings

Each mode has its own settings (port, shared folders, ...), and `mode` says
which one `asc start` uses. The user's choices live in `config.toml`, made by
`asc setup`. Never run `asc setup`
yourself: it asks questions (and exits 2 without a terminal), and it replaces
all their settings. Use `asc config` instead:

```
asc config list                                # every setting and its source
asc config set mode sandbox                    # asc start uses sandbox mode
asc config set share C:\src\app --mode sandbox
asc config set settle_ms 800                   # timings are global
```

- `asc start` uses the `mode` setting unless you pass `--mode M`, with that
  mode's settings. One daemon runs at a time, and the other commands talk to
  it without `--mode`.
- If presses keep missing speech, or a slow app times out, raise `settle_ms`,
  `timeout_ms` or `launch_timeout_ms` rather than passing the
  flag every time. Tell the user what you changed.
- A mode's setting, or `panic_key`, applies to a running daemon at its next `asc start` (it says so).

## Workflow

```
asc config list                   # the mode and its settings
asc start                         # prints status; exit 4: asc already runs
asc mark                          # everything after this counts
...act (click, navigate, press keys, run the test)...
asc wait "(?i)saved"              # returns as soon as a line matches, else exit 1
asc speech                        # all lines since the mark
asc stop                          # when done
```

- To try another mode once, `asc start --mode desktop`.
- If `asc start` exits 4, `asc status` shows which mode runs. Stop it first
  only if it's the wrong one.
- `wait` takes a Python regex, searched, `(?i)` for case. Default timeout 5000
  ms (`--timeout`, or the `timeout_ms` setting). On timeout it prints what NVDA
  did say: read it before retrying.
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

## Launching an app

```
asc launch "C:\share\app\run.cmd" --title "My App"
```

- `launch` starts the command and waits until NVDA says something matching
  `--title` (or anything), then for quiet. It works in every mode. Default timeout 30000 ms (`launch_timeout_ms`).
- Outside sandbox mode, a `launch` that exits 1 ends with `New windows:`. A
  title there means the app started anyway. In watch mode its window may be
  behind another one, so NVDA said nothing until the user switches to it.
- Sandbox mode: the command runs inside the sandbox, which has no Edge and no
  Notepad. Your own app must come from a folder listed in sandbox mode's `share`
  (`asc config set share DIR --mode sandbox`), seen inside as
  `C:\share\<folder name>`.

## Pressing keys (desktop, sandbox or shared)

```
asc press Tab
asc press NVDA+Tab --expect "(?i)button"
asc press H H "Control+Home"
asc type "hello"
```

- Gestures are NVDA's names joined by `+`: `NVDA+T`, `Control+Home`,
  `NVDA+Shift+Up`. Every gesture is checked before any is sent; an unknown key
  exits 2 with suggestions.
- Each press waits for quiet (`--settle`, default 400 ms) and prints only the new
  speech. `--expect REGEX` exits 1 unless it matched.
- Look commands up instead of guessing: `asc keys heading`, `asc keys report
  title`. In browse mode single letters jump (`h` heading, `k` link, `f` form
  field); add Shift to go back.
- Desktop mode: `asc get-nvda` must have been run once (exit 7 says so).
- Sandbox mode: one sandbox at a time, and `asc start` takes about a minute.
  `asc sandbox-setup` must have been run once (exit 7 says so). It shows an
  admin prompt, so ask the user to run it.
- A mode with `launch = false` (NVDA started elsewhere, which connects by
  itself) needs neither: no portable NVDA and no sandbox, and `launch` runs
  the command on this computer.
- Shared mode needs `asc start --mode shared --i-understand` every time.
- Shared mode and desktop mode (with its own NVDA) send keys to the user's
  desktop. Tell the user the panic key before sending anything: `asc status`
  shows it (Control+Shift+Alt+F12 unless `panic_key` in config.toml changes it).

## Exit codes

0 ok, 1 wait or `--expect` failed, 2 usage, 3 not connected or not running, 4
refused or busy, 5 protocol error, 6 bug in asc (the message says where the log is),
7 setup needed (tell the user the hint; retrying won't help), 130 interrupted
(Ctrl+C). On 3, run `asc status`; it says how to connect.
