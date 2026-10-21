# `asc` command reference

`asc` (Agent Speech Check) lets an agent, a person, or CI use NVDA to inspect an application's accessibility through the speech NVDA actually produces. Use it to press keys, read speech as text, and assert expected output.

This is the complete command and configuration reference. If you're new to `asc`, start with the [README](../README.md) first.

## Usage

```
asc [--help] [--version] COMMAND [OPTIONS] [ARGS]
```

- In the synopses, `[X]` is optional, `UPPERCASE` is a value you supply,
  `{a,b}` means pick one, `A | B` means one or the other, and `X ...` can
  repeat. Options may go before or after the arguments.
- `asc --help` lists the commands, `asc COMMAND --help` describes one, and
  `asc --version` prints the version.
- Results go to stdout and errors to stderr. An error prints
  `error: <message>` and, when there is one, `hint: <what to do>`. Bad
  arguments do too, with the command's `--help` as the hint, and exit 2.
- `asc start`, `asc keys` and `asc config` take `--mode` (see
  [Choosing a mode](#choosing-a-mode)). The rest talk to the one daemon that
  runs, whatever its mode.
- `MS` values are milliseconds. A flag on the command beats the setting it
  defaults to (see [Command defaults](#command-defaults)).
- Commands that need NVDA exit 3 if it isn't connected or drops meanwhile, and
  5 if its connection broke the protocol. Commands that send keys (`press`,
  `type`) refuse with exit 4 in watch mode.
- Set `ASC_DEBUG=1` to print the traceback for an internal error (exit 6).

## Commands

| Group | Command | Does |
|---|---|---|
| Recording | [`start`](#asc-start) | start recording in a mode |
| | [`stop`](#asc-stop) | stop recording and forget the speech |
| | [`status`](#asc-status) | show whether `asc` is recording and NVDA is connected |
| Speech | [`speech`](#asc-speech) | print what NVDA said after a line |
| | [`wait`](#asc-wait) | wait until NVDA says something matching `REGEX` |
| | [`mark`](#asc-mark) | print the latest line number |
| Input | [`press`](#asc-press) | press keys and print what NVDA said |
| | [`type`](#asc-type) | type text and print what NVDA said |
| | [`keys`](#asc-keys) | look up NVDA commands in NVDA's own reference |
| | [`launch`](#asc-launch) | start an app and wait until NVDA speaks |
| Setup | [`get-nvda`](#asc-get-nvda) | make the portable NVDA that desktop mode launches |
| | [`setup`](#asc-setup) | choose your settings, by answering questions |
| | [`config`](#asc-config) | show or change one setting |
| | [`sandbox-setup`](#asc-sandbox-setup) | allow the sandbox's NVDA to reach `asc` (asks for admin once) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | A `wait` or `--expect` didn't match, or `launch` timed out |
| 2 | Usage error |
| 3 | Not connected or not running: retry, or `asc start` |
| 4 | Refused, or busy (the message says which) |
| 5 | Protocol error |
| 6 | Internal error (a bug in `asc`; the message says where the log is) |
| 7 | Setup needed: NVDA, Windows Sandbox or its firewall rule; the hint says how |
| 130 | Interrupted (Ctrl+C) |

## Files

| Path | Holds |
|---|---|
| `%LOCALAPPDATA%\agent-speech-check\config.toml` | the settings (see [Configuration](#configuration)) |
| `%LOCALAPPDATA%\agent-speech-check\<mode>\` | one mode's state file, `daemon.log`, and the relay certificate and channel key |
| `%LOCALAPPDATA%\agent-speech-check\nvda\portable` | the portable NVDA that `asc get-nvda` makes |

The certificate and key are created once and kept, because NVDA pins the
certificate.

The MSI installer stops every `asc` daemon before it upgrades, repairs or
removes `asc`. Removing it also deletes this whole folder, unless you run
`msiexec /x FILE.msi KEEPDATA=1`; an upgrade keeps it. The firewall rule from
`asc sandbox-setup` (`agent-speech-check sandbox relay`) stays: delete it in
Windows Defender Firewall.

## Modes

The `mode` setting (or `asc start --mode`) picks one of four.

### Watch mode

Your own NVDA, installed or portable, as it runs now. `asc` listens and never sends keys.

- Connect once in NVDA: Tools, Remote Access, Connect; choose to allow this computer to be controlled, enter the host and key `asc start` printed, and
  trust the certificate when NVDA asks. The host, key and certificate stay the
  same for the mode.
- `asc` never writes your NVDA config.
- `asc` never launches your installed NVDA: a second NVDA restarts the running
  one.
- To press keys in your running NVDA, use shared mode.

### Desktop mode

`asc` launches a portable NVDA (made by `asc get-nvda`, or your own: the
mode's `nvda_dir`) with its own config folder and
`nvda.ini`. That NVDA starts unattended, uses the silent synth, mutes its own
sounds, uses the desktop keyboard layout, and connects to the relay by itself.
`asc stop` ends it, and it always ends when `asc` stops, even after a crash.
Its keys land on this desktop, so it has a [panic key](#panic-key).

- **Your own portable NVDA** (`nvda_dir` in `config.toml`) keeps your settings and add-ons: its `userConfig` folder is copied into the private config folder, and `asc` writes its own settings on top. Your `userConfig` is only read. `asc` replaces your `[remote]` section,
  the synth, sound volume, keyboard layout, mode sounds and the welcome and
  update prompts. It leaves out `profileTriggers.ini`, so no profile switches
  on by itself.
- It needs NVDA 2026.2 or later. Remote Access is built in from 2025.1, but
  2025.1 forwards no speech without an audio device (see `docs/findings.md`).
  An older NVDA exits 7. An `nvda.ini` that `asc` can't read exits 2 and names
  the line.
- It refuses with exit 4 while any `nvda.exe` runs, because starting a second
  NVDA restarts the running one. Use watch mode next to your own NVDA.
- Exits 7 if there is no portable NVDA or it is too old, with the hint
  `asc get-nvda`, or to check `nvda_dir` when you named the folder.
- Before it starts, `asc start` asks NV Access for the latest NVDA (at most
  5 s; offline it says nothing). If that is newer than the portable NVDA, at a
  terminal it offers to update `asc`'s own copy first; a failed update keeps the
  old copy. Without a terminal, as when an agent runs it, it prints
  `NVDA <version> is available: asc get-nvda when asc is stopped` and starts.
  An NVDA in `nvda_dir` is never updated: the line says `(nvda_dir is older)`.
- With `launch = false`, `asc` doesn't start NVDA, so the notes above don't
  apply (no check for a running `nvda.exe`). You start an NVDA yourself
  on this computer and connect it to `asc`. It has to be on this computer
  because the relay only accepts connections from this computer (127.0.0.1).
  `nvda_dir` is refused with exit 2, as in watch and shared mode.
- If that NVDA is your own, `asc`'s keys land on your desktop with no panic key
  and no `--i-understand`. To drive your own NVDA, use shared mode.

### Sandbox mode

`asc` runs NVDA inside Windows Sandbox, so its keys never reach your desktop
and your own NVDA keeps running undisturbed.

- Needs Windows Pro, Enterprise or Education (exit 7 on Home), and
  `asc sandbox-setup` done once (exit 7 with that hint otherwise).
- Only one sandbox can run at a time (exit 4 if one already runs).
- If the sandbox fails to start, a retry may help when wsb timed out or the
  last sandbox was still shutting down (exit 3). An error from wsb itself, or
  NVDA's start script failing inside, exits 7: retrying won't help.
- `asc start` downloads the latest NVDA installer if it has a newer one than
  the one it kept (only a file download, checked against the SHA-1 NV Access
  lists). Offline, it uses the kept one. Then it starts the sandbox, mutes it,
  makes a portable NVDA inside, and returns once NVDA is connected. That takes about a minute. `asc stop` ends the sandbox,
  and so does the daemon ending any other way (even killed).
- With `nvda_dir`, the sandbox runs your own portable
  NVDA instead: the folder is mapped read-only, copied to `C:\nvda` inside, and
  started with a copy of your config, as in desktop mode. Nothing is
  downloaded. Your NVDA on the host may keep running. Exits 7 with the hint
  to check `nvda_dir` if that NVDA is missing or too old.
- The sandbox window is hidden in the system tray; its "asc sandbox" icon
  shows or hides it. `--window taskbar` (or `window = "taskbar"`) keeps it
  minimised on the taskbar instead, and `--window show` leaves it open, but a
  visible window takes focus and catches keys you type. Don't close it: NVDA
  runs in it.
- The relay listens only on this computer's Hyper-V Default Switch address
  (looked up at every start), on port 16837 unless sandbox mode's `port` says another.
- Folders listed in sandbox mode's `share` are mapped read-only into the sandbox
  at `C:\share\<folder name>`, so `asc launch` can start your app from there.

### Shared mode

`asc` drives your own NVDA, so keys go into your real Windows session. Connect
your NVDA to `asc` as in watch mode.

- It refuses with exit 4 unless you pass `--i-understand` to `asc start`, every time. It is never kept in `config.toml`.
- It has a [panic key](#panic-key).

### Panic key

In shared mode, and in desktop mode when `asc` launches its NVDA on this
computer, `asc`'s keys land in your real Windows session. While such a mode
runs, the global panic hotkey **Control+Shift+Alt+F12** releases any held keys
and stops `asc` at once (in desktop mode, its NVDA too).

- `panic_key` in `[defaults]` picks another
  (`asc config set panic_key Control+Alt+F11`). A running daemon keeps its key
  until the next `asc start`.
- If another program owns the hotkey, `asc start` refuses (exit 4): `asc` never
  sends keys to this desktop without one.
- `asc status` shows the panic key.
- Sandbox mode has none: its keys land in the sandbox. Desktop mode with
  `launch = false` has none either (see [Desktop mode](#desktop-mode)).

## Configuration

Settings live in `config.toml` (see [Files](#files)). Make it with `asc setup`
(questions, for people) and change one setting at a time with `asc config`
(for scripts and agents). Both rewrite the whole file, so comments added by
hand are not kept.

```toml
# asc settings. Change them with asc config set, or start over with asc setup.
mode = "sandbox"

[defaults]
settle_ms = 800
timeout_ms = 8000

[sandbox]
share = ['C:\src\app']
nvda_settings = ["keyboard.speakTypedCharacters=False"]
```

The file holds only what was set: a missing key means the built-in default.
Paths are TOML literal strings (`'C:\nvda'`). Unknown keys, and values that
don't fit the tables below, are errors (exit 2) for every command, not only for
the mode that uses them.

### Choosing a mode

`asc start` uses `--mode`, or the `mode` setting, or watch. Each mode keeps its
own settings in a table named after it (`[watch]`, `[desktop]`, `[sandbox]`,
`[shared]`) and its own folder. A mode without a table uses the defaults below.

One daemon runs at a time. `asc start` exits 4 while one runs in any mode, and
`stop`, `status`, `speech`, `wait`, `mark`, `press`, `type` and `launch` talk
to it without being told its mode.

### Global settings

| Setting | Default | Meaning |
|---|---|---|
| `mode` | watch | the mode `asc start` uses without `--mode` |
| `panic_key` | Control+Shift+Alt+F12 | the [panic key](#panic-key), written as for `asc press`: Control, Alt or Windows (Shift may join them, NVDA can't), then one key that isn't a modifier |

### Command defaults

In `[defaults]`. A flag on the command beats the setting, which beats the
built-in default.

| Setting | Default | Flag |
|---|---|---|
| `settle_ms` | 400 (50 to 10000) | `press`, `type --settle` |
| `timeout_ms` | 5000 (at most 600000) | `wait`, `press`, `type --timeout` |
| `launch_timeout_ms` | 30000 (at most 600000) | `launch --timeout` |
| `keys_layout` | desktop | `keys --layout` (desktop or laptop) |

### Mode settings

In the mode's table, like `[sandbox]`. [Modes](#modes) explains each one in
context.

| Setting | Default | Meaning |
|---|---|---|
| `port` | watch and shared 6837, sandbox 16837, desktop a free one | relay port |
| `launch` | true in desktop and sandbox mode | false: `asc` starts no NVDA; you start one and connect it (in desktop mode, on this computer) |
| `nvda_dir` | `asc get-nvda`'s | desktop or sandbox: your portable NVDA, a full path |
| `share` | none | sandbox: folders mapped read-only to `C:\share\<name>`, full paths |
| `window` | tray | sandbox: its window in the tray, taskbar or show |
| `nvda_settings` | none | `section.key=value` settings for an NVDA that `asc` launches, as `--nvda-setting` takes |

`window` and `share` are refused (exit 2) outside sandbox mode, and `nvda_dir`
and `nvda_settings` for an NVDA that `asc` doesn't launch. The NVDA settings
`asc` writes itself are never kept.

## Command reference

### `asc start`

```
asc start [--mode {watch,desktop,sandbox,shared}]
          [--nvda-setting SECTION.KEY=VALUE] [--window {tray,taskbar,show}]
          [--i-understand]
```

Starts the daemon in the mode and prints its status. What it starts and
checks depends on the mode: see [Modes](#modes).

| Option | Default | Meaning |
|---|---|---|
| `--mode MODE` | the `mode` setting, or watch | the mode to start, with its settings |
| `--nvda-setting SECTION.KEY=VALUE` | the mode's `nvda_settings` | an `nvda.ini` setting for the NVDA `asc` launches (repeatable) |
| `--window {tray,taskbar,show}` | sandbox mode's `window`, or tray | sandbox only: its window, for this start only |
| `--i-understand` | off | shared mode: yes, send keys into my own session |

- `--nvda-setting` adds to the mode's `nvda_settings`; for the same key,
  `--nvda-setting` wins. Names are letters, digits and `_`, with dots for
  nested sections. Settings `asc` writes itself (`remote`, the synth, sound
  volume, keyboard layout, the mode sounds) are refused with exit 2, and so is
  a setting for an NVDA `asc` doesn't launch.
- Exits 4 if `asc` is already running, in this mode or another (the message
  names that mode), and 3 with the last daemon log lines if it fails to start.

```shell
asc start
asc start --mode sandbox
asc start --nvda-setting keyboard.speakTypedCharacters=False
```

### `asc stop`

```
asc stop
```

Releases any held keys, stops the daemon and forgets the speech, and prints
`stopped: MODE`. Exits 3 if nothing runs.

- A daemon that is there but doesn't answer (an older asc, or a stuck one)
  still counts as running, so `asc start` and `asc setup` refuse with exit 4. `asc stop` ends its process,
  with its NVDA or sandbox, but can't release held keys that way. The same
  happens to a daemon still there 30 s after agreeing to stop.
- If Windows won't let it end the process, it exits 4 and keeps the state file.

### `asc status`

```
asc status
```

Shows the mode, whether `asc` is recording and NVDA is connected, the
browse/focus mode and the speech log:

```
mode: watch (keys refused)
recording: yes
nvda: connected
browse/focus: unknown
speech: 12 lines kept, latest 12
```

- When NVDA isn't connected it prints how to connect it.
- `browse/focus` is `unknown` until NVDA first switches modes.
- A `last error:` line says why the last failed connection was dropped (for
  example a wrong channel key), until NVDA next joins.
- When the mode has a [panic key](#panic-key), it shows it.
- If `asc` isn't running, it exits 3.

### `asc speech`

```
asc speech --since N
```

Prints what NVDA said after line `N`, one numbered line per utterance, and
mode changes:

```
11 said: "Recycle Bin list item focused 1 of 2"
12 mode: focus
```

| Option | Default | Meaning |
|---|---|---|
| `--since N` | (required) | lines after line `N` (`0` for everything still kept) |

Prints `(nothing said after N)` when there is nothing. If lines after `N` were dropped
(the log keeps 10,000, and one reply about 32 MB of speech), it first prints
`(K earlier lines dropped)`. An `N` after the latest line exits 2: line numbers
restart with each `asc start`, so it can only come from an earlier session.

### `asc wait`

```
asc wait --since N [--timeout MS] REGEX
```

Returns as soon as an utterance after line `N` matches `REGEX`, and prints it
with its number. Pass that number as the next `--since`, so the next `wait`
can't match the same line.

| Argument or option | Default | Meaning |
|---|---|---|
| `REGEX` | (required) | Python syntax, searched anywhere in the line; use `(?i)` to ignore case |
| `--timeout MS` | `timeout_ms`, 5000 | how long to wait, at most 600000 |
| `--since N` | (required) | look at lines after line `N`: this step's mark, or old speech can match |

- On timeout it exits 1 and prints what NVDA did say, numbered like `speech`,
  so its last number is still the next `--since`.
- An `N` after the latest line exits 2, as in `speech`.
- If NVDA disconnects meanwhile, `wait` keeps waiting, since NVDA reconnects by
  itself. If it's still gone at the timeout, `wait` exits 3 instead of 1, with
  the last connection error if there was one. A connection that never joins
  (not NVDA, as far as `asc` can tell) never fails a `wait`.

```shell
asc wait --since 40 "OK button"
asc wait --since 40 "(?i)saved" --timeout 10000
```

### `asc mark`

```
asc mark
```

Prints the number of the latest line: `mark: 12`. Run it before an action, then
pass the number to `speech` or `wait` as `--since`. Nothing is stored, so callers
can't move each other's position:

```shell
asc mark                      # mark: 40
# ...act...
asc wait --since 40 "Saved"   # 43 said: "File saved"
asc wait --since 43 "Done"    # waits for speech after line 43
```

`press`, `type` and `launch` take no `--since`: they print only what NVDA said
while they ran, numbered the same way. Their last number, or `N` in
`(nothing said after N)`, is the next `--since`:

```shell
asc press Tab                 # 41 said: "OK button"
asc press Enter               # 42 said: "Saving"
asc wait --since 42 "Saved"   # 43 said: "File saved"
```

### `asc press`

```
asc press [--expect REGEX] [--settle MS] [--timeout MS] KEY [KEY ...]
```

Sends each gesture, waits for `--settle` ms of quiet, and prints only the new
speech as numbered `said: "..."` and `mode: ...` lines, like `speech`, or
`(nothing said after N)`, where `N` is the latest line before the first key.
Like `speech`, it notes `(K earlier lines dropped)` when the log or one reply
could not hold them all.

| Argument or option | Default | Meaning |
|---|---|---|
| `KEY` | (required) | a gesture: key names joined by `+` |
| `--expect REGEX` | none | unless the new speech matches, exit 1 and print what was said, numbered |
| `--settle MS` | `settle_ms`, 400 | quiet period, 50 to 10000 |
| `--timeout MS` | `timeout_ms`, 5000 | deadline for the whole command, including waiting for the keyboard |

- Gestures are written as NVDA writes them: `Tab`, `H`, `NVDA+Tab`,
  `Control+Home`, `NVDA+Shift+Up`. Case doesn't matter. Aliases such as
  `Ctrl`, `Esc`, `Del`, `PgDn` and `Win` work. Numpad keys are sent as NVDA's
  desktop layout expects them, with Num Lock off. Look commands up with
  [`asc keys`](#asc-keys).
- Every gesture is checked before the first is sent. An unknown key exits 2
  with suggestions.
- Two presses never interleave. The second waits for the first, within its own
  `--timeout`, and exits 4 if it can't get the keyboard.

```shell
asc press Tab
asc press H H --expect "level 2"
asc press Control+Home NVDA+Down
```

### `asc type`

```
asc type [--settle MS] [--timeout MS] TEXT
```

Types `TEXT` one character at a time, mapped to keys with this computer's
keyboard layout, then waits for one quiet period at the end. Prints the new
speech like `press`. If any character can't be typed (for example emoji), it
exits 2, lists them and sends nothing.

| Argument or option | Default | Meaning |
|---|---|---|
| `TEXT` | (required) | the text; newlines become Enter and tabs become Tab |
| `--settle MS` | `settle_ms`, 400 | quiet period at the end |
| `--timeout MS` | `timeout_ms`, 5000 | deadline for the whole command, including waiting for the keyboard |

```shell
asc type "hello world"
asc type "x@example.com" --settle 800
```

### `asc keys`

```
asc keys [--mode {watch,desktop,sandbox,shared}] [--layout {desktop,laptop}] [--lang LANG]
         [--braille | --no-braille] [SEARCH ...]
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
| `--mode MODE` | the `mode` setting, or watch | whose `nvda_dir` to read |
| `--layout {desktop,laptop}` | `keys_layout`, desktop | the keyboard layout's keys |
| `--lang LANG` | en | reference language, like `de` or `pt_BR`, falling back to `en` |
| `--braille`, `--no-braille` | off | include rows for braille displays |

- It reads `documentation\<lang>\keyCommands.html` from the mode's portable
  NVDA (desktop, or sandbox with `nvda_dir`), else from your own NVDA (the
  running `nvda.exe`, the uninstall registry entry, then Program Files). If
  none is found it exits 7 and says where it looked.
- Single letter navigation keys (`h  heading`) work in browse mode; add Shift
  to go back.

```shell
asc keys heading
asc keys --layout laptop current line
```

### `asc launch`

```
asc launch [--title REGEX] [--timeout MS] CMD
```

Starts `CMD` and waits until NVDA says something matching `--title`, or
anything at all if `--title` is left out, then for quiet. Prints what NVDA
said, numbered like `press`. Works in every mode.

| Argument or option | Default | Meaning |
|---|---|---|
| `CMD` | (required) | the command to start |
| `--title REGEX` | anything | wait until NVDA says this |
| `--timeout MS` | `launch_timeout_ms`, 30000 | how long to wait, at most 600000 |

- On timeout it exits 1 with what NVDA did say, numbered. Outside sandbox mode the
  message ends with `New windows:`, the titles of windows that opened
  meanwhile, or `(none)`.
- A command that can't start exits 2. NVDA disconnecting meanwhile is handled
  as in `wait`.
- In sandbox mode (unless `launch = false`) `CMD` runs inside the sandbox,
  which has no Edge and no Notepad. Your own app must come from a path under
  `C:\share\`; programs the sandbox already has, like `mshta.exe`, need no
  shared folder. A `.cmd` or `.bat` runs in a hidden console, so NVDA hears
  the app it starts, not the console.
- `launch` brings the new window forward so NVDA hears it, except in watch
  mode, where it may stay behind.

```shell
asc launch "C:\share\app\run.cmd" --title "My App"
asc launch "py -m myapp" --title "My App" --timeout 60000
```

### `asc get-nvda`

```
asc get-nvda [--version NVDA_VERSION] [--installer FILE]
```

Downloads the NVDA installer from NV Access and makes a portable copy in
`%LOCALAPPDATA%\agent-speech-check\nvda\portable` for desktop mode. A finished
download deletes the kept installers of other versions. The new copy replaces
the old one only once it is complete, so a failed run keeps the old one.

| Option | Default | Meaning |
|---|---|---|
| `--version NVDA_VERSION` | the latest | the NVDA version, shaped like `2026.2` (else exit 2) |
| `--installer FILE` | none | use this local installer, no download |

- It refuses with exit 4 while any `nvda.exe` runs, because the installer
  would restart it.
- Without `--version`, it asks NV Access for the latest stable NVDA and checks
  the download against the SHA-1 NV Access lists. If NV Access can't be
  reached, it uses the installer it kept from the last download.
- A failed download exits 3.

```shell
asc get-nvda
asc get-nvda --version 2026.2
asc get-nvda --installer nvda_2026.2.exe
```

### `asc setup`

```
asc setup [--yes]
```

Asks a few questions and saves the answers in `config.toml`:

1. The mode.
2. Desktop and sandbox mode: which NVDA.
3. Sandbox mode: the shared folders and the window.
4. Watch and shared mode: your NVDA's keyboard layout.

When they are missing, it offers to download NVDA for desktop mode and to add
the sandbox firewall rule. Timings are not asked; change them with
`asc config set` when needed.

| Option | Default | Meaning |
|---|---|---|
| `--yes` | off | replace existing settings without asking |

- Each question lists numbered choices and marks the default; Enter keeps it.
  Answer with the number or name.
- Running it again replaces all settings, after asking. Mode folders, with
  the relay certificate NVDA trusts, are kept.
- Nothing is written until the last question, `Save?`; Ctrl+C stops without
  saving (exit 130).
- Without a terminal it exits 2 at once, so a script never waits on it. Use
  `asc config` there.
- It refuses with exit 4 while `asc` runs.

### `asc config`

```
asc config list [--mode MODE]
asc config get NAME [--mode MODE]
asc config set NAME VALUE [VALUE ...] [--mode MODE]
asc config unset NAME [--mode MODE]
asc config path
```

Shows or changes the settings in `config.toml` (see
[Configuration](#configuration)).

| Action | Does |
|---|---|
| `list` | prints every setting as `name = value (source)`; the source is the mode's name, `global` or `default` |
| `get` | prints the value only |
| `set` | changes it and prints `name = value (where)` |
| `unset` | goes back to the inherited value |
| `path` | prints where `config.toml` is (works even when the file is broken) |

- Mode settings go to the table of `--mode`, or of the `mode` setting (watch
  unless set). Global settings (`mode`, `panic_key`) and command defaults take
  no `--mode` (exit 2); `panic_key` and command defaults go to `[defaults]`.
- Unsetting a mode's last setting removes its table.
- Lists (`share`, `nvda_settings`) take several values and replace the whole
  list. Paths are made full paths. Booleans are `true` or `false`.
- The whole file is checked before it is written, so a clash (for example
  `window show` in watch mode) exits 2 and changes nothing. An unknown setting
  exits 2 and suggests close names.
- A running mode's settings apply at its next `asc start` (it says so).
  Command defaults apply at once.

```shell
asc config list
asc config set mode sandbox
asc config set share C:\src\app C:\data --mode sandbox
asc config unset settle_ms
```

### `asc sandbox-setup`

```
asc sandbox-setup
```

Creates the one Windows Firewall rule sandbox mode needs, after a Windows admin
prompt. Sandbox mode's NVDA connects to `asc` on this computer's Hyper-V
Default Switch address, which Windows Firewall blocks until this rule allows
it. The rule is inbound TCP, only on the `vEthernet (Default Switch)`
interface, only sandbox mode's relay port (16837 unless its `port` says another), only for the
program that runs `asc`.

- If the rule is already in place it says so and changes nothing.
- Run it again after updating Python or `asc`, since the rule names the
  program.
- Remove it from an admin terminal with
  `netsh advfirewall firewall delete rule name="agent-speech-check sandbox relay"`.
