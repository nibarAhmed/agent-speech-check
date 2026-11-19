---
name: asc
description: Check what a screen reader (NVDA on Windows, Orca on Linux) actually says, or drive an app with its keys, using asc (Agent Speech Check). Use when verifying screen reader output, live regions, focus order, labels, or keyboard navigation in a Windows, Linux or web app.
---

# asc: hear what the screen reader says

`asc` records every utterance of the screen reader, NVDA on Windows or Orca on
Linux, as numbered lines. If `asc` isn't on PATH, ask the user to install it (the
MSI on Windows, the tarball on Linux, as the README says) from
https://github.com/nibarAhmed/agent-speech-check/releases/latest. `asc --help` and
`asc <command> --help` are short and exact; trust them over this file.

## Mode

`asc start` uses the configured mode (`asc config` shows it). Pass `--mode`
only when the user asks for a mode. If the configured mode can't do the task,
stop and ask the user to switch to the mode this table suggests:

| Need | Mode |
|---|---|
| Hear the user's own screen reader while you act some other way (browser tools, tests, the user) | `watch` (never sends keys) |
| Press keys yourself, and no NVDA runs on this machine | `desktop` (needs `asc get-nvda` once) |
| Press keys yourself on Linux | `desktop` (the default there: its own Orca on a hidden display) |
| Press keys yourself while the user's NVDA runs | `sandbox` (NVDA in Windows Sandbox, which has no browser; `asc start` takes about a minute) |
| Press keys in the user's real session | `shared`, only when the user explicitly asks in this conversation; only then add `--i-understand` |
| Press keys on a machine the user set up, over ssh | `remote` with `os windows` (NVDA, the user starts and connects it; `launch` is refused) or `os linux` (Orca) |

On Linux only desktop, watch and remote mode exist.

## Rules

- Never run a screen reader binary or installer on the host yourself: a second
  one replaces the user's screen reader. `asc get-nvda` and `asc start` are safe.
- Never stop the user's screen reader.
- Never run `asc setup`: it asks questions and replaces all the user's settings.
  Change one setting with `asc config set NAME VALUE [--mode M]`, and tell the user
  what you changed.
- `asc firewall-setup` (sandbox mode) shows an admin prompt and adds a Windows Firewall rule.
  Ask the user first, unless they already said to go ahead, then run it
  yourself; they approve the prompt.
- Shared and Windows desktop mode send keys to the user's desktop. Tell the user the
  panic key from `asc status` before you send any.
- If the user names their own portable NVDA, set it for desktop or sandbox mode
  (`asc config set nvda_dir DIR --mode desktop`); then `asc get-nvda` isn't needed.
- Never set `launch` yourself. Desktop mode with `launch = false` starts no
  NVDA (the user starts one): it has no panic key, yet its keys reach an NVDA
  on this computer, maybe the user's own: send keys only if the user asked.
- Remote mode (configured, or the user names a machine) runs only in a named
  instance. It leaves the user's settings alone, so set it up without asking
  to switch modes. Use your own instance, named after the machine, with
  `--instance NAME` (or `ASC_INSTANCE=NAME`) on every command, `asc stop`
  too. Never share one with another agent. Set it up yourself:
  1. `asc config set mode remote --instance NAME`, then
     `asc config set ssh USER@HOST --mode remote --instance NAME` and
     `asc config set os windows --mode remote --instance NAME` (`linux` for Orca).
  2. `asc start --instance NAME`. ssh never asks for a password: if it exits
     7 about ssh, ask the user to run `ssh USER@HOST` once. Otherwise follow its
     hint, then start again.
  3. With NVDA, tell the user what `asc status --instance NAME` says, so the
     machine's NVDA can connect through the tunnel.

## Workflow

```
asc start                         # the configured mode
asc mark                          # mark: 40, before each step
...act (click, navigate, run the test)...
asc wait --since 40 "(?i)saved"
asc speech --since 40             # everything after the mark
asc stop                          # when done
```

- For `--since`, pass this step's mark, or N from `(nothing said after N)`.
  Numbers restart with each `asc start`.
- If `asc start` says `asc` already runs, another session may own it: stop it
  only if it's the wrong mode.
- If a press's speech appears only in the next command's output, raise
  `settle_ms`; if a slow app times out, raise `timeout_ms` or
  `launch_timeout_ms` (`asc config set`).

## Pressing keys

`NVDA+` and `Orca+` name the same modifier, the screen reader's own. Common
commands (Orca's desktop layout, the one asc's own Orca uses):

| To | NVDA | Orca |
|---|---|---|
| Name the window in front | `NVDA+T` | `Orca+NumpadEnter` |
| Say where the focus is | `NVDA+Tab` | `NumpadEnter` |
| Switch browse and focus mode | `NVDA+Space` | `Orca+A` |
| Say the time | `NVDA+F12` | `Orca+T` |

`asc keys WORDS` searches the screen reader's own key reference, as in
`asc keys browse mode`; add `--layout laptop` for a laptop layout. In remote
mode it reads the other machine's: pass the same `--instance`.

- Before a key that acts (Enter, Space, `type`), confirm the target: reach it
  with `--expect`, as in `asc press Tab --expect "(?i)send"`, and act only if
  that exited 0; otherwise check where the focus is. A loop acts only after it
  matched; if it runs out, stop and look.
- After starting or switching apps, name the window in front.
- A press that says `unknown` or `window unavailable`, or a screen reader
  command doing the app's own thing, means the app lacks focus, not that keys
  are slow: bring it to the front, then retry with `--expect`.
- Silence after an action may be the finding: confirm the action happened
  before blaming the key.
- `asc status` shows `browse/focus`, the mode the screen reader last switched to.

## Watch and shared mode with NVDA

`asc start` asks the user's running NVDA to connect and can take over a minute,
so give the command a timeout of two minutes or more:
when it prints `sent NVDA a link`, tell the user to answer Yes in NVDA
(and to trust the certificate the first time). If it ends with
`nvda: not connected`, the user didn't answer or NVDA couldn't be asked: have
them connect in NVDA (Tools, Remote Access, Connect, allow this computer to be
controlled, the host and key `asc status` prints), then check `asc status`.
Until NVDA restarts it reconnects by itself, in watch and in shared mode: they
use the same key. Watch mode records everything
NVDA says, typed text included, so `asc stop` when you're done.

## Apps

- A `launch` that times out but lists a title under `New windows:` started
  anyway. In watch mode the user may need to switch to that window.
- Sandbox mode: your own app must come from a folder in sandbox mode's `share`
  (`asc config set share DIR --mode sandbox`), seen inside as `C:\share\<name>`.
- Remote mode with NVDA: the machine's NVDA connects only after the user ticks
  Enable Remote Access in its settings, then connects as `asc status` says. Or,
  in a VM you set up yourself, save `asc status --nvda-ini` as UTF-8 `nvda.ini`
  in its NVDA's config folder while NVDA is not running (Windows PowerShell
  5.1's `>` writes UTF-16): it then connects at startup, nothing to answer.
  Start the app on the VM yourself or ask the user, then `asc wait`.

## Orca

- Desktop mode starts its own Orca on a hidden display, so its keys reach no
  one's desktop. `asc launch CMD` starts the app on that display; there's no
  other way to reach it.
- It needs Orca 51 or later on that machine (`orca --version`), plus Xvfb,
  openbox, xdotool and python3. If `asc start` exits 7 naming one, ask the
  user to install or upgrade it: older Orca can't work.
- To hear the user's own Orca, use watch mode. If `asc start --mode watch`
  exits 7 with a restart line, ask the user to run it in their desktop
  session (it restarts their Orca with asc's secret), then start again. Never
  run it yourself: it replaces the Orca they rely on.

## Exit codes

| Code | Meaning | Next step |
|---|---|---|
| 1 | `wait`, `--expect` or `launch` didn't match | read what the screen reader did say |
| 2 | usage | fix the arguments or setting |
| 3 | not connected or not running | follow the hint: `asc start` if not running, else `asc status` says how to connect |
| 4 | refused or busy | read the message |
| 5 | protocol error | report it |
| 6 | bug in asc | report it; the message says where the log is |
| 7 | setup needed | follow the hint (tell the user if it needs them); retrying alone won't help |
