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

`asc start` uses the configured mode (`asc config list` shows it). Pass `--mode`
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
- Never run `asc uninstall` unless the user explicitly asks to uninstall `asc`.
- `asc firewall-setup` (sandbox mode) shows an admin prompt and adds a Windows Firewall rule.
  Ask the user first, unless they already said to go ahead, then run it
  yourself; they approve the prompt.
- Shared and Windows desktop mode send keys to the user's desktop. Tell the user the
  panic key from `asc status` before you send any.
- If the user names their own portable NVDA, set it for desktop or sandbox mode
  (`asc config set nvda_dir DIR --mode desktop`); then `asc get-nvda` isn't needed.
- Never change `launch` yourself. In desktop mode with `launch = false`, `asc`
  starts no NVDA (the user connects one manually), so there is no panic key even
  though keys may reach the user's desktop. Send keys only if the user explicitly
  asked.
- Use remote mode when it is already configured or when the user asks to test on
  another machine. Run `asc status --all` first to see any already-configured or
  running instances. Because remote mode runs in its own named instance, setting it
  up does not touch the user's default settings, so you do not need to ask before
  configuring it. Pick an instance name for the machine (never share one with
  another agent) and pass `--instance NAME` (or set `ASC_INSTANCE=NAME`) on every
  command, including `asc stop`:
  1. `asc config set mode remote --instance NAME`, then
     `asc config set ssh USER@HOST --mode remote --instance NAME` and
     `asc config set os windows --mode remote --instance NAME` (`linux` for Orca).
  2. `asc start --instance NAME`. Because `ssh` runs non-interactively (no
     password prompts), if it exits 7 with an SSH error, ask the user to run
     `ssh USER@HOST` once to confirm host trust and key login. For other errors,
     follow the printed hint and retry.
  3. With NVDA, tell the user what `asc status --instance NAME` prints so the
     remote NVDA can connect through the tunnel.

## Workflow

```
asc start                         # the configured mode
asc mark                          # mark: 40, before each step
...act (click, navigate, run the test)...
asc wait --since 40 "(?i)saved"
asc speech --since 40             # everything after the mark
asc stop                          # when done
```

- For `--since`, pass this step's mark, the last line number printed by the
  previous command, or N from `(nothing said after N)`. Line numbers reset on
  each `asc start`.
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

- Before sending an activating key (Enter, Space, or `type`), confirm focus is
  on the intended control. Use `--expect` when you expect that press to match
  (for example, `asc press Tab --expect "(?i)send"` or asserting an action's
  result): `--expect` waits up to `--timeout` (default 5 s) before failing.
- When stepping through controls with `Tab` or arrow keys to find one, call
  `asc press Tab` without `--expect` (it returns after `settle_ms`) and inspect
  its output rather than discarding it or burning 5 s per miss. If a search
  loop hits its limit without matching, stop and inspect focus before pressing
  anything else.
- After starting or switching apps, check the active window title.
- If a press speaks `unknown` or `window unavailable`, or a screen reader
  shortcut triggers an app action instead, the app does not have focus (it is
  not a timing issue). Bring the app to the front, then retry with `--expect`.
- Silence after an action may itself be an accessibility bug: verify the action
  actually happened before blaming the key.
- `asc status` shows `browse/focus`, the mode the screen reader last switched to.

## Watch and shared mode with NVDA

- Give `asc start` a command timeout of at least 2 minutes: it waits up to 10 s
  for NVDA to reconnect on its own and up to 60 s for the user to approve the
  invite link.
- When it prints `sent NVDA a link`, tell the user to answer Yes in NVDA (and
  trust the certificate on the first connection).
- If it finishes with `nvda: not connected`, ask the user to connect manually in
  NVDA (Tools > Remote Access > Connect > Allow this computer to be controlled)
  using the host and key from `asc status`, then re-check `asc status`.
- Once connected, NVDA reconnects automatically across `watch` and `shared` mode
  (they share the same key) until NVDA restarts.
- Watch mode records everything NVDA says, including typed text, so run
  `asc stop` as soon as you finish.

## Apps

- If `asc launch` times out but lists a title under `New windows:`, the app
  started in the background. In watch mode, ask the user to switch to it.
- Sandbox mode: your own app must live inside a folder configured in sandbox
  mode's `share` (`asc config set share DIR --mode sandbox`), mapped inside the
  sandbox as `C:\share\<name>`.
- Remote mode with NVDA: on the remote machine, the user must enable Remote
  Access in NVDA settings and connect using the details from `asc status`. For a
  VM you manage yourself, save `asc status --nvda-ini` as UTF-8 `nvda.ini` in
  NVDA's config folder while NVDA is closed so it connects automatically at
  startup (avoid `>` in Windows PowerShell 5.1, which writes UTF-16). Start the
  app on the VM or ask the user to, then run `asc wait`.

## Orca

- Desktop mode starts its own Orca on a hidden display, so its keys never reach
  anyone's desktop. Use `asc launch CMD` to start an app on that display.
- Requires Orca 51 or later (`orca --version`), plus Xvfb, openbox, xdotool,
  dbus-daemon, gdbus, and python3. If `asc start` exits 7 naming a missing tool
  or old Orca, ask the user to install or upgrade it.
- To hear the user's own Orca, use watch mode. If `asc start --mode watch` exits
  7 with a restart command, ask the user to run it in their desktop session (it
  restarts their Orca with `asc`'s recording secret), then run `asc start` again.
  Never run that command yourself: it restarts the screen reader they rely on.

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
