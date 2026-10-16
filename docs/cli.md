# `asc` command reference

`asc` (Agent Speech Check) lets an agent, a person or CI use NVDA the way a
blind user does: press keys, read what NVDA says as text, and check it. This
file covers all four modes: watch, desktop, sandbox and shared.

## Synopsis

```
asc [--help] [--version] COMMAND [ARGS] [OPTIONS]
```

`asc --help` lists the commands, `asc COMMAND --help` describes one, and
`asc --version` prints the version.

## Commands

| Group | Command | Does |
|---|---|---|
| Recording | [`start`](#asc-start) | start recording for a target |
| | [`stop`](#asc-stop) | stop recording and forget the speech |
| | [`status`](#asc-status) | show whether `asc` is recording and NVDA is connected |
| Speech | [`speech`](#asc-speech) | print what NVDA said since the mark |
| | [`wait`](#asc-wait) | wait until NVDA says something matching `REGEX` |
| | [`mark`](#asc-mark) | remember the current end of the speech log |
| | [`clear`](#asc-clear) | drop all kept speech |
| Input | [`press`](#asc-press) | press keys and print what NVDA said |
| | [`type`](#asc-type) | type text and print what NVDA said |
| | [`keys`](#asc-keys) | look up NVDA commands in NVDA's own reference |
| | [`launch`](#asc-launch) | start an app and wait until NVDA speaks |
| Setup | [`get-nvda`](#asc-get-nvda) | make the portable NVDA that desktop mode launches |
| | [`setup`](#asc-setup) | choose your settings, by answering questions |
| | [`config`](#asc-config) | show or change one setting |
| | [`sandbox-setup`](#asc-sandbox-setup) | allow the sandbox's NVDA to reach `asc` (asks for admin once) |

## Conventions

- Results go to stdout and errors to stderr. An error prints
  `error: <message>` and, when there is one, `hint: <what to do>`. Bad
  arguments do too, with the command's `--help` as the hint, and exit 2.
- Every command except `asc setup`, `asc config path` and
  `asc config remove-target` takes `--target NAME` (default: `default`, or the
  `default` setting). See [Targets](#targets).
- `MS` values are milliseconds. A flag on the command beats the setting it
  defaults to (see [Command defaults](#command-defaults)).

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

## Environment

| Variable | Effect |
|---|---|
| `ASC_DEBUG=1` | print the traceback for an internal error (exit 6) |

## Files

| Path | Holds |
|---|---|
| `%LOCALAPPDATA%\agent-speech-check\config.toml` | the settings (see [Configuration](#configuration)) |
| `%LOCALAPPDATA%\agent-speech-check\<target>\` | one target's state file, `daemon.log`, and the relay certificate and channel key |
| `%LOCALAPPDATA%\agent-speech-check\nvda\portable` | the portable NVDA that `asc get-nvda` makes |

The certificate and key are created once and kept, because NVDA pins the
certificate.

## Modes

The target's `mode` (or `asc start --mode`) picks one of four.

### Watch mode

Your own NVDA, installed or portable, as it runs now. `asc` listens and never sends keys.

- Connect once in NVDA: Tools, Remote Access, Connect; choose to allow this computer to be controlled, enter the host and key `asc start` printed, and
  trust the certificate when NVDA asks. The host, key and certificate stay the
  same for the target.
- `asc` never writes your NVDA config.
- `asc` never launches your installed NVDA: a second NVDA restarts the running
  one.
- To press keys in your running NVDA, use shared mode.

### Desktop mode

`asc` launches a portable NVDA (made by `asc get-nvda`, or your own: the
target's `nvda_dir` or `--nvda-dir`) with its own config folder and
`nvda.ini`. That NVDA starts unattended, uses the silent synth, mutes its own
sounds, uses the desktop keyboard layout, and connects to the relay by itself.
`asc stop` ends it, and it always ends when `asc` stops, even after a crash.

- **Your own portable NVDA** (`--nvda-dir DIR`, or `nvda_dir` in
  `config.toml`) keeps your settings and add-ons: its `userConfig` folder is copied into the private config folder, and `asc` writes its own settings on top. Your `userConfig` is only read. `asc` replaces your `[remote]` section,
  the synth, sound volume, keyboard layout, mode sounds and the welcome and
  update prompts. It leaves out `profileTriggers.ini`, so no profile switches
  on by itself. `--nvda-dir` is not kept; it holds for this start only.
- It needs NVDA 2026.2 or later. Remote Access is built in from 2025.1, but
  2025.1 forwards no speech without an audio device (see `docs/protocol.md`).
  An older NVDA exits 7. An `nvda.ini` that `asc` can't read exits 2 and names
  the line.
- It refuses with exit 4 while any `nvda.exe` runs, because starting a second
  NVDA restarts the running one. Use watch mode next to your own NVDA.
- Exits 7 if there is no portable NVDA, with the hint `asc get-nvda`, or to
  check `--nvda-dir` / `nvda_dir` when you named the folder. With
  `launch = false` (below) no portable NVDA is needed.
- With `launch = false`, NVDA is started elsewhere (a VM, another machine) and
  connects to the relay itself. `nvda_dir` and `--nvda-dir` are then refused
  with exit 2, as in watch and shared mode.

### Sandbox mode

`asc` runs NVDA inside Windows Sandbox, so its keys never reach your desktop
and your own NVDA keeps running undisturbed.

- Needs Windows Pro, Enterprise or Education (exit 7 on Home), and
  `asc sandbox-setup` done once (exit 7 with that hint otherwise).
- Only one sandbox can run at a time (exit 4 if one already runs).
- If the sandbox fails to start, a retry may help when wsb timed out or the
  last sandbox was still shutting down (exit 3). An error from wsb itself, or
  NVDA's start script failing inside, exits 7: retrying won't help.
- `asc start` downloads the NVDA installer if needed (only a file download),
  starts the sandbox, mutes it, makes a portable NVDA inside, and returns once
  NVDA is connected. That takes about a minute. `asc stop` ends the sandbox,
  and so does the daemon ending any other way (even killed).
- With `--nvda-dir DIR` (or `nvda_dir`), the sandbox runs your own portable
  NVDA instead: the folder is mapped read-only, copied to `C:\nvda` inside, and
  started with a copy of your config, as in desktop mode. Nothing is
  downloaded. Your NVDA on the host may keep running.
- The sandbox window is hidden in the system tray; its "asc sandbox" icon
  shows or hides it. `--window taskbar` (or `window = "taskbar"`) keeps it
  minimised on the taskbar instead, and `--window show` leaves it open, but a
  visible window takes focus and catches keys you type. Don't close it: NVDA
  runs in it.
- The relay listens only on this computer's Hyper-V Default Switch address
  (looked up at every start), on port 16837 unless the target sets `port`.
- Folders listed in the target's `share` are mapped read-only into the sandbox
  at `C:\share\<folder name>`, so `asc launch` can start your app from there.

### Shared mode

`asc` drives your own NVDA, so keys go into your real Windows session. Connect
your NVDA to `asc` as in watch mode.

- It refuses with exit 4 unless you pass `--i-understand` to `asc start`, every time. It is never kept in `config.toml`.
- While it runs, the global panic hotkey **Control+Shift+Alt+F12** releases any held keys and stops `asc` at once. If another program owns that hotkey, shared
  mode refuses to start (exit 4): it never runs without one.
- `asc status` shows the panic key.

## Configuration

Settings live in `config.toml` (see [Files](#files)). Make it with `asc setup`
(questions, for people) and change one setting at a time with `asc config`
(for scripts and agents). Both rewrite the whole file, so comments added by
hand are not kept.

```toml
# asc settings. Change them with asc config set, or start over with asc setup.
default = "app"

[defaults]
timeout_ms = 8000

[targets.app]
mode = "sandbox"
share = ['C:\src\app']
nvda_settings = ["keyboard.speakTypedCharacters=False"]
settle_ms = 800
```

The file holds only what was set: a missing key means the built-in default.
Paths are TOML literal strings (`'C:\nvda'`). Unknown keys, and values that
don't fit the tables below, are errors (exit 2) for every command, not only for
the one that uses that target.

### Targets

A target is a named setup: a mode and its settings, with its own daemon and folder. Commands use the target named by `--target`, or the `default` setting,
or `default`. A target that isn't listed gets the defaults for `--mode` (watch
if left out). For a listed target, `--mode` must match its `mode`, or
`asc start` exits 2.

### Global settings

| Setting | Default | Meaning |
|---|---|---|
| `default` | `default` | the target used without `--target` |
| `nvda_version` | 2026.2 | the NVDA `asc get-nvda` downloads |

### Command defaults

In `[defaults]`, and a target may override them. A flag on the command beats
the target's value, which beats `[defaults]`, which beats the built-in default.

| Setting | Default | Flag |
|---|---|---|
| `settle_ms` | 400 (50 to 10000) | `press`, `type --settle` |
| `timeout_ms` | 5000 (at most 600000) | `wait`, `press`, `type --timeout` |
| `launch_timeout_ms` | 30000 (at most 600000) | `launch --timeout` |
| `keys_layout` | desktop | `keys --layout` (desktop or laptop) |
| `keys_lang` | en | `keys --lang` |
| `keys_braille` | false | `keys --braille`, `--no-braille` |

### Target settings

In `[targets.NAME]`.

| Setting | Default | Meaning |
|---|---|---|
| `mode` | (required) | watch, desktop, sandbox or shared |
| `port` | watch and shared 6837, sandbox 16837, desktop a free one | relay port |
| `keys` | false in watch mode, else true | may send keys; never true in watch mode |
| `launch` | true in desktop and sandbox mode | false if NVDA is started elsewhere and connects itself |
| `nvda_dir` | `asc get-nvda`'s | desktop or sandbox: your portable NVDA, a full path |
| `share` | none | sandbox: folders mapped read-only to `C:\share\<name>`, full paths |
| `window` | tray | sandbox: its window in the tray, taskbar or show |
| `nvda_settings` | none | `section.key=value` settings for an NVDA that `asc` launches, as `--nvda-setting` takes |

`window` and `share` are refused (exit 2) outside sandbox mode, and `nvda_dir`
and `nvda_settings` for an NVDA that `asc` doesn't launch.

Never kept: shared mode's `--i-understand` (it is asked for at every start),
and the NVDA settings `asc` writes itself.

## Command reference

### `asc start`

```
asc start [--target NAME] [--mode {watch,desktop,sandbox,shared}]
          [--nvda-setting SECTION.KEY=VALUE] [--nvda-dir DIR]
          [--window {tray,taskbar,show}] [--i-understand]
```

Starts the daemon for the target and prints its status. What it starts depends
on the mode: see [Modes](#modes).

| Option | Default | Meaning |
|---|---|---|
| `--mode MODE` | the target's `mode`, or watch | mode for an unlisted target |
| `--nvda-setting SECTION.KEY=VALUE` | the target's `nvda_settings` | an `nvda.ini` setting for the NVDA `asc` launches (repeatable) |
| `--nvda-dir DIR` | the target's `nvda_dir` | desktop or sandbox: launch this portable NVDA |
| `--window {tray,taskbar,show}` | the target's `window`, or tray | sandbox: its window in the tray, on the taskbar, or shown |
| `--i-understand` | off | shared mode: yes, send keys into my own session |

- `--nvda-setting` adds to the target's `nvda_settings`; for the same key,
  `--nvda-setting` wins. Names are letters, digits and `_`, with dots for
  nested sections. Settings `asc` writes itself (`remote`, the synth, sound
  volume, keyboard layout, the mode sounds) are refused with exit 2, and so is
  a setting for an NVDA `asc` doesn't launch.
- `--nvda-dir` and `--window` hold for this start only. `--window` is refused
  (exit 2) outside sandbox mode.

Exits 4 if the target is already running, and 3 with the last daemon log lines
if it fails to start. Each mode adds its own checks.

```
asc start
asc start --target app
asc start --nvda-setting keyboard.speakTypedCharacters=False
```

### `asc stop`

```
asc stop [--target NAME]
```

Releases any held keys, stops the daemon and forgets the speech.

A daemon that is there but doesn't answer (an older asc, or one that is stuck)
still counts as running: `asc start`, `asc setup` and `asc config remove-target`
refuse with exit 4. `asc stop` then ends its process, along with its NVDA or
sandbox. It does the same to a daemon that agreed to stop but is still there
30 s later. It can't release held keys that way. If Windows won't let it end
the process, it exits 4 and keeps the state file.

### `asc status`

```
asc status [--target NAME]
```

Shows the target, whether `asc` is recording and NVDA is connected, the
browse/focus mode and the speech log:

```
target: default (watch mode, keys refused)
recording: yes
nvda: connected
browse/focus: unknown
speech: 12 lines kept, latest 12, mark 10
```

- When NVDA isn't connected it prints how to connect it.
- `browse/focus` is `unknown` until NVDA first switches modes.
- A `last error:` line says why the last failed connection was dropped (for
  example a wrong channel key), until NVDA next joins.
- In shared mode it shows the panic key.

If `asc` isn't running, it exits 3 with
`asc is not running for target "default" (not recording)`.

### `asc speech`

```
asc speech [--target NAME] [--since N]
```

Prints what NVDA said after the mark, one numbered line per utterance, and
mode changes:

```
11 said: "Recycle Bin list item focused 1 of 2"
12 mode: focus
```

| Option | Default | Meaning |
|---|---|---|
| `--since N` | the mark | lines after line `N` |

Prints `(nothing said)` when there is nothing. If lines after `N` were dropped
(the log keeps 10,000), it first prints `(K earlier lines dropped)`.

```
asc speech
asc speech --since 0       everything still kept
asc speech --since 41      lines after 41
```

### `asc wait`

```
asc wait [--target NAME] [--timeout MS] [--since N] REGEX
```

Returns as soon as an utterance after the mark matches `REGEX`, and prints it.

| Argument or option | Default | Meaning |
|---|---|---|
| `REGEX` | (required) | Python syntax, searched anywhere in the line; use `(?i)` to ignore case |
| `--timeout MS` | `timeout_ms`, 5000 | how long to wait, at most 600000 |
| `--since N` | the mark | look at lines after line `N` |

- On timeout it exits 1 and prints what NVDA did say.
- Exits 3 if NVDA isn't connected, and 5 if NVDA's connection broke the
  protocol.
- If NVDA disconnects meanwhile, `wait` keeps waiting, since NVDA reconnects by
  itself. If it's still gone at the timeout, `wait` exits 3 instead of 1, with
  the last connection error if there was one.
- A connection that never joins (not NVDA, as far as `asc` can tell) never
  fails a `wait`; it only shows in `asc status`.

```
asc wait "OK button"
asc wait "(?i)saved" --timeout 10000
```

### `asc mark`

```
asc mark [--target NAME]
```

Sets the mark to the latest line and prints it: `mark: 12`. `speech` and `wait`
look at lines after the mark.

### `asc clear`

```
asc clear [--target NAME]
```

Drops all kept speech. Line numbers keep counting.

### `asc press`

```
asc press [--target NAME] [--expect REGEX] [--settle MS] [--timeout MS] KEY [KEY ...]
```

Sends each gesture, waits for `--settle` ms of quiet, and prints only the new
speech as `said: "..."` and `mode: ...` lines, or `(nothing said)`.

| Argument or option | Default | Meaning |
|---|---|---|
| `KEY` | (required) | a gesture: key names joined by `+` |
| `--expect REGEX` | none | exit 1 unless the new speech matches |
| `--settle MS` | `settle_ms`, 400 | quiet period, 50 to 10000 |
| `--timeout MS` | `timeout_ms`, 5000 | deadline for the whole command, including waiting for the keyboard |

- Gestures are written as NVDA writes them: `Tab`, `H`, `NVDA+Tab`,
  `Control+Home`, `NVDA+Shift+Up`. Case doesn't matter. Aliases such as
  `Ctrl`, `Esc`, `Del`, `PgDn` and `Win` work. Numpad keys are sent as NVDA's
  desktop layout expects them, with Num Lock off. Look commands up with
  [`asc keys`](#asc-keys).
- Every gesture is checked before the first is sent. An unknown key exits 2
  with suggestions.
- `--expect` that doesn't match exits 1 and prints what was said.
- Watch mode refuses with exit 4. Exits 3 if NVDA isn't connected or drops
  during the press, and 5 if NVDA broke the protocol.
- Two presses never interleave. The second waits for the first, within its own
  `--timeout`, and exits 4 if it can't get the keyboard.

```
asc press Tab
asc press NVDA+Tab
asc press H H --expect "level 2"
asc press Control+Home NVDA+Down
```

### `asc type`

```
asc type [--target NAME] [--settle MS] [--timeout MS] TEXT
```

Types `TEXT` one character at a time, mapped to keys with this computer's
keyboard layout, then waits for one quiet period at the end.
Prints the new speech like `press`.

| Argument or option | Default | Meaning |
|---|---|---|
| `TEXT` | (required) | the text; newlines become Enter and tabs become Tab |
| `--settle MS` | `settle_ms`, 400 | quiet period at the end |
| `--timeout MS` | `timeout_ms`, 5000 | deadline for the whole command, including waiting for the keyboard |

- Every character is checked first: if any can't be typed (for example emoji),
  it exits 2, lists them and sends nothing.
- Watch mode refuses with exit 4.

```
asc type "hello world"
asc type "x@example.com" --settle 800
```

### `asc keys`

```
asc keys [--target NAME] [--layout {desktop,laptop}] [--nvda-dir DIR]
         [--lang LANG] [--braille | --no-braille] [SEARCH ...]
```

Prints commands from NVDA's own Commands Quick Reference whose name, keys,
description or section contain every `SEARCH` word, one per line, or
`(no match)`. It needs no running `asc`.

```
NVDA+t  Report title: Reports the title of the currently active window. ...
h  heading
```

| Argument or option | Default | Meaning |
|---|---|---|
| `SEARCH` | none (all) | words that must all appear |
| `--layout {desktop,laptop}` | `keys_layout`, desktop | the keyboard layout's keys |
| `--nvda-dir DIR` | the target's NVDA | read this NVDA's reference |
| `--lang LANG` | `keys_lang`, en | reference language, falling back to `en` |
| `--braille`, `--no-braille` | `keys_braille`, false | include rows for braille displays |

- The reference is `documentation\<lang>\keyCommands.html` of the NVDA the
  target uses. For a desktop target, or a sandbox target with `nvda_dir`, that
  is its portable NVDA. Otherwise it is your own NVDA: the running `nvda.exe`,
  then the NVDA uninstall registry entry, then Program Files. `--nvda-dir`
  overrides this. If none is found it exits 7 and says where it looked.
- Single letter navigation keys (`h  heading`) work in browse mode; add Shift
  to go back.

```
asc keys heading
asc keys report title
asc keys --layout laptop current line
```

### `asc launch`

```
asc launch [--target NAME] [--title REGEX] [--timeout MS] CMD
```

Starts `CMD` and waits until NVDA says something matching `--title`, or
anything at all if `--title` is left out, then for quiet. Prints what NVDA
said. Works in every mode.

| Argument or option | Default | Meaning |
|---|---|---|
| `CMD` | (required) | the command to start |
| `--title REGEX` | anything | wait until NVDA says this |
| `--timeout MS` | `launch_timeout_ms`, 30000 | how long to wait, at most 600000 |

- On timeout it exits 1 with what NVDA did say. Outside sandbox mode the
  timeout message ends with `New windows:`, the titles of windows that opened
  meanwhile, or `(none)`: a window listed there started.
- A command that can't start exits 2. NVDA disconnecting or breaking the
  protocol meanwhile is handled as in `wait` (exit 3 or 5).
- In sandbox mode (unless `launch = false`) `CMD` runs inside the sandbox,
  which has no Edge and no Notepad. Your own app must come from a path under
  `C:\share\`; programs the sandbox already has, like `mshta.exe`, need no
  shared folder. A `.cmd` or `.bat` `CMD` runs in a hidden console, so NVDA
  hears the app it starts, not the batch file's console.
- In desktop, shared and sandbox mode, `launch` brings the new window forward
  so NVDA hears it. A target with `keys = false` (always so in watch mode)
  can't, so there the window may stay behind.

```
asc launch "C:\share\app\run.cmd" --title "My App"
asc launch "py -m myapp" --title "My App" --timeout 60000
```

### `asc get-nvda`

```
asc get-nvda [--target NAME] [--version NVDA_VERSION] [--installer FILE]
```

Downloads the NVDA installer from NV Access and makes a portable copy in
`%LOCALAPPDATA%\agent-speech-check\nvda\portable` for desktop mode. A finished
download deletes the installers of other versions it kept.

| Option | Default | Meaning |
|---|---|---|
| `--version NVDA_VERSION` | `nvda_version`, 2026.2 | the NVDA version, shaped like `2026.2` |
| `--installer FILE` | none | use this local installer, no download |

- A `--version` not shaped like `2026.2` exits 2.
- It refuses with exit 4 while any `nvda.exe` runs, because the installer
  would restart it.
- A failed download exits 3.

```
asc get-nvda
asc get-nvda --installer nvda_2026.2.exe
```

### `asc setup`

```
asc setup [--yes]
```

Asks a few questions and saves the answers in `config.toml`, for the default
target:

1. The mode.
2. Desktop and sandbox mode: which NVDA.
3. Sandbox mode: the shared folders and the window.
4. Watch and shared mode: your NVDA's keyboard layout.

When they are missing, it offers to download NVDA for desktop mode and to add
the sandbox firewall rule. Timings are not asked: the defaults work, and the
right value depends on the app. Change them with `asc config set` when needed.

| Option | Default | Meaning |
|---|---|---|
| `--yes` | off | replace existing settings without asking |

- Each question comes first, then its numbered choices, with `(default)` after
  the default one, then the prompt (`Choose 1 to 4:`). Enter keeps the
  default. Answer with the number or name.
- Notes about a mode (shared mode's `--i-understand`, desktop mode with
  another NVDA running) come right after it is picked.
- Running it again replaces all settings, after asking. Target folders, with
  the relay certificate NVDA trusts, are kept.
- Nothing is written until the last question, `Save?`; Ctrl+C stops without
  saving (exit 130).
- It needs a terminal: without one it exits 2 at once, so a script never
  waits on it. Use `asc config` there.
- It refuses with exit 4 while any target runs; `asc stop` it first.

### `asc config`

```
asc config list [--target NAME]
asc config get NAME [--target NAME]
asc config set NAME VALUE [VALUE ...] [--target NAME]
asc config unset NAME [--target NAME]
asc config remove-target TARGET
asc config path
```

Shows or changes the settings in `config.toml` (see
[Configuration](#configuration)).

| Action | Does |
|---|---|
| `list` | prints every setting as `name = value (source)`; the source is `target`, `global` or `default` |
| `get` | prints the value only |
| `set` | changes it and prints `name = value (where)` |
| `unset` | goes back to the inherited value |
| `remove-target` | deletes the target's settings (not its folder) |
| `path` | prints where `config.toml` is (works even when the file is broken) |

- Target settings go to `--target`, or to the default target. Command defaults
  go to `[defaults]`, or with `--target` to that target. Global settings take
  no `--target` (exit 2).
- A target is made by setting its `mode`; any other setting for a target that
  isn't listed exits 2 with that hint. `unset mode` is refused (exit 2).
- Lists (`share`, `nvda_settings`) take several values and replace the whole
  list. Paths are made full paths. Booleans are `true` or `false`.
- The whole file is checked before it is written, so a clash (for example
  `keys true` in watch mode) exits 2 and changes nothing.
- Changing a running target's setting prints that it applies at the next
  `asc start`. Command defaults apply at once. `remove-target` refuses with
  exit 4 while the target runs.
- An unknown setting exits 2 and suggests close names.

```
asc config list
asc config set settle_ms 800 --target app
asc config set mode sandbox --target app      makes target app
asc config set share C:\src\app C:\data --target app
asc config unset settle_ms --target app
```

### `asc sandbox-setup`

```
asc sandbox-setup [--target NAME]
```

Creates the one Windows Firewall rule sandbox mode needs, after a Windows admin
prompt. Sandbox mode's NVDA connects to `asc` on this computer's Hyper-V
Default Switch address, which Windows Firewall blocks until this rule allows
it. The rule is inbound TCP, only on the `vEthernet (Default Switch)`
interface, only the target's relay port (16837 by default), only for the
program that runs `asc`.

- If the rule is already in place it says so and changes nothing.
- Run it again after updating Python or `asc`, since the rule names the
  program.
- Remove it with
  `Remove-NetFirewallRule -DisplayName 'agent-speech-check sandbox relay'` in
  an admin PowerShell.

```
asc sandbox-setup
asc sandbox-setup --target app
```
