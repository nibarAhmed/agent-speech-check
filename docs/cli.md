# `asc` command reference

Covers watch, desktop, sandbox and shared mode. This file is updated with every
command change.

Results go to stdout and errors to stderr. An error prints `error: <message>`
and, when there is one, `hint: <what to do>`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | A `wait` or `--expect` failed |
| 2 | Usage error |
| 3 | Not connected or not running: retry, or `asc start` |
| 4 | Refused, or busy (the message says which) |
| 5 | Protocol error |
| 6 | Internal error (a bug in `asc`; the message says where the log is) |
| 7 | Setup needed: NVDA, Windows Sandbox or its firewall rule; the hint says how |
| 130 | Interrupted (Ctrl+C) |

A unit test checks this table against `src/agent_speech_check/errors.py`.

Set `ASC_DEBUG=1` to print the traceback for an internal error.

## Targets

Every command takes `--target NAME` (default: `default`, or `default = "..."`
in `targets.toml`). `targets.toml` sits in `%LOCALAPPDATA%\agent-speech-check\`:

```toml
default = "app"

[targets.app]
mode = "watch"      # watch, desktop, sandbox or shared
port = 6837         # relay port; watch 6837, sandbox 16837, desktop a free one
keys = false        # may send keys; never true in watch mode
launch = true       # desktop: false if NVDA is started elsewhere and connects itself
nvda_dir = 'C:\nvda' # desktop or sandbox: your portable NVDA (default: asc get-nvda's)
share = ['C:\src\app'] # sandbox: folders mapped read-only to C:\share\<name>
window = "tray"     # sandbox: its window in the tray (default), taskbar or show
```

Unknown keys are errors. `nvda_dir` must be a full path (exit 2 otherwise).
`window` is refused (exit 2) outside sandbox mode. A target that isn't listed gets the defaults for
`--mode` (watch if left out).

Each target has a folder `%LOCALAPPDATA%\agent-speech-check\<target>\` with the
state file, `daemon.log`, and the relay certificate and channel key. The
certificate and key are created once and kept, because NVDA pins the
certificate.

## Commands

### `asc start [--mode watch|desktop|sandbox|shared] [--nvda-setting S] [--nvda-dir DIR] [--window tray|taskbar|show] [--i-understand]`

Starts the daemon for the target and prints its status. Exits 4 if it is
already running, and 3 with the last daemon log lines if it fails to start.

**Watch mode** is your own NVDA, installed or portable, as it runs now. `asc`
listens and never sends keys. Connect once in NVDA: Tools, Remote Access,
Connect; choose to allow this computer to be controlled, enter the host and key
`asc start` printed, and trust the certificate when NVDA asks. `asc` never
writes your NVDA config. To press keys in your running NVDA, use shared mode.
`asc` never launches your installed NVDA: a second NVDA restarts the running one.

**Desktop mode** launches a portable NVDA (made by `asc get-nvda`, or your own:
the target's `nvda_dir` or `--nvda-dir`) with its own config folder and
`nvda.ini`. That NVDA starts unattended, uses the silent synth, mutes its own
sounds, uses the desktop keyboard layout, and connects to the relay by itself.
It runs in a Windows job, so it dies with the daemon even after a crash.
`asc stop` ends it.
- **Your own portable NVDA** (`--nvda-dir DIR`, or `nvda_dir` in
  `targets.toml`) keeps your settings and add-ons: its `userConfig` folder is
  copied into the private config folder, and `asc` writes its own settings on
  top. Your `userConfig` is only read. `asc` replaces your `[remote]` section,
  the synth, sound volume, keyboard layout, mode sounds and the welcome and
  update prompts. It leaves out `profileTriggers.ini`, so no profile switches
  on by itself. `--nvda-dir` is not kept; it holds for this start only.
- It needs NVDA 2025.1 or later (Remote Access is built in from then on).
  An older NVDA exits 7. An `nvda.ini` that `asc` can't read exits 2 and names
  the line.
- It refuses with exit 4 while any `nvda.exe` runs, because starting a second
  NVDA restarts the running one. Use watch mode next to your own NVDA.
- Exits 3 if there is no portable NVDA, with the hint `asc get-nvda`, or to
  check `--nvda-dir` / `nvda_dir` when you named the folder.
- `--nvda-setting section.key=value` (repeatable) adds an `nvda.ini` setting,
  for example `--nvda-setting speech.symbolLevel=300`. Names are letters,
  digits and `_`, with dots for nested sections. Settings `asc` writes itself
  (`remote`, the synth, sound volume, keyboard layout, the mode sounds) are
  refused with exit 2, and so is a setting for an NVDA `asc` doesn't launch.
- With `launch = false`, NVDA is started elsewhere (a VM, another machine) and
  connects to the relay itself. `nvda_dir` and `--nvda-dir` are then refused
  with exit 2, as in watch and shared mode.

**Sandbox mode** runs NVDA inside Windows Sandbox, so its keys never reach your
desktop and your own NVDA keeps running undisturbed.
- Needs Windows Pro, Enterprise or Education (exit 7 on Home), and
  `asc sandbox-setup` done once (exit 7 with that hint otherwise).
- Only one sandbox can run at a time (exit 4 if one already runs).
- `asc start` downloads the NVDA installer if needed (only a file download),
  starts the sandbox, mutes it, makes a portable NVDA inside, and returns once
  NVDA is connected. That takes about a minute. `asc stop` ends the sandbox,
  and so does the daemon ending any other way (even killed).
- With `--nvda-dir DIR` (or `nvda_dir`), the sandbox runs your own portable
  NVDA instead: the folder is mapped read-only, copied to `C:\nvda` inside, and
  started with a copy of your config, as in desktop mode. Nothing is
  downloaded. Your NVDA on the host may keep running.
- The sandbox window goes to the system tray: it is hidden, with no taskbar
  button, and an "asc sandbox" tray icon shows or hides it (click it, or select
  it and press Enter). Minimising the shown window sends it back to the tray.
  `--window taskbar` (or `window = "taskbar"`) keeps it minimised on the
  taskbar instead, and `--window show` leaves it alone. A visible sandbox
  window takes focus and catches keys you type. NVDA needs the window's
  session, so the window can't be closed altogether. `--window` holds for this
  start only, and is refused (exit 2) outside sandbox mode. If the tray icon
  can't be added, the window is minimised instead. When the sandbox window
  opens, focus goes back to the window you were in.
- The relay listens only on this computer's Hyper-V Default Switch address
  (looked up at every start), on port 16837 unless the target sets `port`.
- Folders listed in the target's `share` are mapped read-only into the sandbox
  at `C:\share\<folder name>`, so `asc launch` can start your app from there.

**Shared mode** drives your own NVDA, so keys go into your real Windows
session. Connect your NVDA to `asc` as in watch mode.
- It refuses with exit 4 unless you pass `--i-understand` to `asc start`, every
  time. This is never read from `targets.toml`.
- While it runs, the global panic hotkey **Control+Shift+Alt+F12** releases any
  held keys and stops `asc` at once. If another program owns that hotkey, shared
  mode refuses to start (exit 4): it never runs without one.
- `asc status` shows the panic key.

### `asc stop`

Releases any held keys, stops the daemon and forgets the speech.

### `asc status`

```
target: default (watch mode, keys refused)
recording: yes
nvda: connected
browse/focus: unknown
speech: 12 lines kept, latest 12, mark 10
```

When NVDA isn't connected it prints how to connect it. `browse/focus` is
`unknown` until NVDA first switches modes. A `last error:` line says why the
last failed connection was dropped (for example a wrong channel key), until
NVDA next joins. If `asc` isn't running, it exits 3
with `asc is not running for target "default" (not recording)`.

### `asc speech [--since N]`

Prints what NVDA said after the mark (or after line `N`), one numbered line per
utterance, and mode changes:

```
11 said: "Recycle Bin list item focused 1 of 2"
12 mode: focus
```

`(nothing said)` when there is nothing. If lines after `N` were dropped (the log
keeps 10,000), it first prints `(K earlier lines dropped)`.

### `asc wait REGEX [--timeout MS] [--since N]`

Returns as soon as an utterance after the mark (or `N`) matches `REGEX` (Python
syntax, searched; use `(?i)` to ignore case), and prints it. Default timeout
5000 ms, at most 600000. On timeout it exits 1 and prints what NVDA did say.
Exits 3 if NVDA isn't connected, and 5 if NVDA's connection broke the protocol.
If NVDA disconnects meanwhile, `wait` keeps waiting, since NVDA reconnects by
itself. If it's still gone at the timeout, `wait` exits 3 instead of 1, with the
last connection error if there was one. A connection that never joins (not
NVDA, as far as `asc` can tell) never fails a `wait`; it only shows in
`asc status`.

### `asc mark`

Sets the mark to the latest line and prints it: `mark: 12`.

### `asc clear`

Drops all kept speech. Line numbers keep counting.

### `asc press KEY [KEY ...] [--expect REGEX] [--settle MS] [--timeout MS]`

Sends each gesture, waits for `--settle` ms of quiet (default 400, 50 to
10000), and prints only the new speech as `said: "..."` and `mode: ...` lines,
or `(nothing said)`.

- Gestures are key names joined by `+`, as NVDA writes them: `Tab`, `H`,
  `NVDA+Tab`, `Control+Home`, `NVDA+Shift+Up`. Case doesn't matter. Aliases
  such as `Ctrl`, `Esc`, `Del`, `PgDn` and `Win` work. Numpad keys are sent as
  NVDA's desktop layout expects them, with Num Lock off.
- Every gesture is checked before the first is sent. An unknown key exits 2
  with suggestions.
- `--expect` exits 1 unless the new speech matches, and prints what was said.
- Watch mode refuses with exit 4. Exits 3 if NVDA isn't connected or drops
  during the press, and 5 if NVDA broke the protocol.
- Two presses never interleave. The second waits for the first, within its own
  `--timeout` (default 5000 ms), and exits 4 if it can't get the keyboard.

### `asc type TEXT [--settle MS] [--timeout MS]`

Types `TEXT` one character at a time, mapped to keys with this computer's
keyboard layout (`VkKeyScanW`). Newlines become Enter and tabs become Tab.
Every character is checked first: if any can't be typed (for example emoji),
it exits 2 listing them and sends nothing. NVDA gets one quiet period at the
end. Prints the new speech like `press`. Watch mode refuses with exit 4.

### `asc keys [SEARCH ...] [--layout desktop|laptop] [--nvda-dir DIR] [--lang L] [--braille]`

Prints commands from NVDA's own Commands Quick Reference whose name, keys,
description or section contain every `SEARCH` word, one per line:

```
NVDA+t  Report title: Reports the title of the currently active window. ...
h  heading
```

`(no match)` when nothing matches. It needs no running `asc`.
- The reference is `documentation\<lang>\keyCommands.html` (default `en`,
  falling back to `en`) of the NVDA the target uses. For a desktop target, or a
  sandbox target with `nvda_dir`, that is its portable NVDA. Otherwise it is
  your own NVDA: the running `nvda.exe`,
  then the NVDA uninstall registry entry, then Program Files. `--nvda-dir`
  overrides this. If none is found it exits 7 and says where it looked.
- Rows for braille displays are left out unless `--braille` is given.
- Single letter navigation keys (`h  heading`) work in browse mode; add Shift
  to go back.

### `asc launch CMD [--title REGEX] [--timeout MS]`

Starts `CMD` and waits until NVDA says something matching `--title`, or
anything at all if `--title` is left out, then for quiet. Prints what NVDA said.
Default timeout 30000 ms; on timeout it exits 1 with what NVDA did say. A
command that can't start exits 2. NVDA disconnecting or breaking the protocol
meanwhile is handled as in `wait` (exit 3 or 5). Works in watch, desktop and sandbox mode; in
sandbox mode `CMD` runs inside the sandbox, so use paths under `C:\share\`.

### `asc sandbox-setup [--target NAME]`

Creates the one Windows Firewall rule sandbox mode needs, after a Windows admin
prompt: inbound TCP, only on the `vEthernet (Default Switch)` interface, only the
target's relay port (16837 by default), only for the program that runs `asc`.
If the rule is already in place it says so and changes nothing. Run it again
after updating Python or `asc`, since the rule names the program. Remove it with
`Remove-NetFirewallRule -DisplayName 'agent-speech-check sandbox relay'` in an
admin PowerShell.

### `asc get-nvda [--version V] [--installer FILE]`

Downloads the NVDA installer from NV Access (default version 2026.2) and makes a
portable copy in `%LOCALAPPDATA%\agent-speech-check\nvda\portable` for
desktop mode. `--installer` uses a local installer instead of downloading. It
refuses with exit 4 while any `nvda.exe` runs, because the installer would
restart it. A failed download exits 7.
