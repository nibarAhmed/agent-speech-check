# `asc` command reference

`asc` (Agent Speech Check) lets an agent, a person, or [CI](ci.md) use NVDA to inspect an application's accessibility through the speech NVDA actually produces. Use it to press keys, read speech as text, and assert expected output. On Linux it does the same with Orca (see [Desktop mode](#desktop-mode)), and [remote mode](#remote-mode) reaches NVDA or Orca on another machine over ssh.

This is the complete command and configuration reference. If you're new to `asc`, start with the [README](../README.md) first.

## Usage

```
asc [--help] [--version] [--instance NAME] COMMAND [OPTIONS] [ARGS]
```

- In the synopses, `[X]` is optional, `UPPERCASE` is a value you supply,
  `{a,b}` means pick one, `A | B` means one or the other, and `X ...` can
  repeat. Options may go before or after the arguments.
- `asc --help` (`-h`) lists the commands, `asc COMMAND --help` describes one,
  and `asc --version` (`-V`) prints the version.
- Results go to stdout and errors to stderr. An error prints
  `error: <message>` and, when there is one, `hint: <what to do>`. Bad
  arguments do too, with the command's `--help` as the hint, and exit 2.
- `asc start`, `asc keys` and `asc config` take `--mode`
  (see [Modes](#modes)). `stop`, `status`, `speech`, `wait`, `mark`, `press`,
  `type` and `launch` talk to the running daemon for this
  [instance](#instances), regardless of its mode.
- `MS` values are in milliseconds. Command-line flags override the
  corresponding settings in `config.toml` (see [Command defaults](#command-defaults)).
- Commands that need the screen reader fail with exit 3 if it isn't connected,
  drops meanwhile, or breaks the protocol. Commands that send keys (`press`,
  `type`) are refused in watch mode (exit 3).
- Set `ASC_DEBUG=1` to print the traceback for an internal error (exit 4).

## Commands

| Group | Command | Does |
|---|---|---|
| Recording | [`start`](#asc-start) | start recording in a mode |
| | [`stop`](#asc-stop) | stop recording and forget the speech |
| | [`status`](#asc-status) | show whether `asc` is recording and the screen reader is connected |
| Speech | [`speech`](#asc-speech) | print what the screen reader said after a line |
| | [`wait`](#asc-wait) | wait until the screen reader says something matching `REGEX` |
| | [`mark`](#asc-mark) | print the latest line number |
| Input | [`press`](#asc-press) | press keys and print what the screen reader said |
| | [`type`](#asc-type) | type text and print what the screen reader said |
| | [`keys`](#asc-keys) | look up screen reader commands in its own reference |
| | [`launch`](#asc-launch) | start an app and wait until the screen reader speaks |
| Setup | [`get-nvda`](#asc-get-nvda) | make the portable NVDA that desktop mode launches (Windows only) |
| | [`setup`](#asc-setup) | choose your settings, by answering questions |
| | [`config`](#asc-config) | show or change one setting |
| | [`uninstall`](#asc-uninstall) | remove a Linux release install of asc (Linux only) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | A `wait` or `--expect` didn't match, or `launch` timed out |
| 2 | Usage or configuration error |
| 3 | Operational error (not running, disconnected, refused, busy, setup needed, I/O or protocol; follow the message and hint) |
| 4 | Internal error (a bug in `asc`; the message says where the log is) |
| 130 | Interrupted (Ctrl+C) |

## Modes

`asc start` uses `--mode`, the `mode` setting, or the platform default (`watch`
on Windows, `desktop` on Linux). There are four modes (`watch`, `desktop`,
`remote`, and `shared`, which is Windows-only and exits 2 on Linux). Each mode
keeps its own folder and settings (see [Configuration](#configuration)).

### Watch mode

Records speech from your own running screen reader without ever sending keys.

#### Windows (NVDA)

- `asc start` invites your running NVDA to connect. NVDA opens a dialog asking
  "Do you wish to allow this computer to be controlled", and on the first
  connection also asks you to trust the certificate: answer Yes (`asc` cannot
  answer for you). `asc start` prints `sent NVDA a link: ...` and waits up to
  one minute for your approval.
- Once NVDA has connected, it reconnects automatically until it is restarted.
  To avoid an unnecessary prompt, `asc start` waits up to 10 s for an automatic
  reconnect before sending a link.
- If no NVDA is running, `asc start` skips the invite and returns immediately.
  If NVDA is running without Remote Access enabled, `asc` cannot invite it and
  returns after the 10 s wait. You can connect manually in NVDA (Tools > Remote
  Access > Connect > Allow this computer to be controlled) using the host and
  key printed by `asc status`. The host, key, and certificate stay the same
  across restarts and are shared with shared mode.
- If NVDA is already busy with another Remote Access session, it refuses the
  invite link. The same can happen if watch mode and shared mode are configured
  with different `port` values and NVDA is still trying to reconnect to the
  previous mode's port. To recover, disconnect in NVDA (Tools > Remote Access >
  Disconnect), then run `asc stop` and `asc start`.
- Run `asc config set connect false` to disable automatic invites (for example,
  if NVDA's own Remote Access settings already connect it at startup).
- `asc` never modifies your NVDA config and never launches your installed NVDA
  (starting a second NVDA would restart your running one).
- To press keys in your running NVDA, use [shared mode](#shared-mode-windows).

#### Linux (Orca)

Records your own running Orca (51 or later). Orca only shares speech with
programs that present a recording secret set in Orca's environment at startup:

- `asc start` generates that secret once and saves it in
  `~/.local/state/agent-speech-check/orca-secret` (readable only by you).
- If no Orca is running, `asc start` fails and never starts one from scratch.
- If your running Orca was started without `asc`'s secret, what `asc start`
  does depends on `restart_orca` in `[watch]` (default `ask`):
  - `ask`: in an interactive terminal inside your desktop session, asks before
    restarting Orca with the secret; without a terminal (for example, when run
    by an agent), fails with a hint to run `asc start --mode watch` in a
    terminal once or enable `restart_orca` `always`.
  - `always`: restarts your running Orca automatically when needed (even when
    run by an agent), as long as a desktop display (`DISPLAY` or
    `WAYLAND_DISPLAY`) is present.
  - `never`: never restarts Orca and prints the restart command for you to run.
- Your Orca keeps the secret until it exits, so a restart is needed at most
  once per login session. If `asc` cannot see a desktop display, it never
  restarts Orca and prints the restart command instead.
- Run `asc` from inside your desktop session so it can reach Orca over D-Bus.
- `asc launch CMD` starts `CMD` on your desktop, and the app keeps running
  after `asc stop`.
- `asc stop` ends recording while leaving your Orca running.

### Desktop mode

`asc` starts and manages its own screen reader on this machine: NVDA on
Windows, Orca on Linux.

#### Windows (NVDA)

`asc` launches a portable NVDA (created by `asc get-nvda`, or your own via
`nvda_dir`) with an isolated config folder and `nvda.ini`. That NVDA starts
unattended with the silent synthesizer, muted sounds, desktop keyboard layout,
and automatic connection to `asc`'s relay. It exits whenever `asc` stops, even
after a crash. Because its keys go to your desktop, Windows desktop mode
registers a [panic key](#panic-key).

- **Using your own portable NVDA** (`nvda_dir` in `config.toml`) preserves your
  settings and add-ons: `asc` copies its `userConfig` folder into a private
  config folder and overlays `asc`'s managed settings (see
  [Mode settings](#mode-settings)), disabling welcome and update prompts. Your
  original `userConfig` is never modified. `profileTriggers.ini` is skipped so
  application-specific profiles do not switch automatically.
- Requires NVDA 2026.2 or later. Remote Access was built in starting in 2025.1,
  but 2025.1 forwards no speech without an audio device (see
  [findings.md](findings.md#nvda-20251)). Older NVDA versions are refused; an
  unreadable `nvda.ini` exits 2 and reports the offending line.
- Refuses to start while any `nvda.exe` is running, because starting a
  second NVDA restarts the running one. Use watch or remote mode alongside
  your own NVDA.
- Fails if the portable NVDA is missing or too old, hinting `asc get-nvda`
  (or to check `nvda_dir` if you configured a custom folder).
- Before launching NVDA, `asc start` checks NV Access for a newer release (for
  at most 5 s; skipped silently when offline). If a newer version exists:
  - In an interactive terminal, it offers to update `asc`'s managed copy before
    starting (if the update fails, the existing copy is kept).
  - Without a terminal (for example, when run by an agent), it prints
    `NVDA <version> is available: asc get-nvda when asc is stopped` and
    continues starting.
  - A custom NVDA in `nvda_dir` is never auto-updated; the notice appends
    `(nvda_dir is older)`.

#### Linux (Orca)

`asc` starts Orca on a private, hidden X display (Xvfb with openbox as the
window manager), so keys never reach your desktop and no panic key is needed. No
audio device is required: `asc` records what Orca would speak.

- Requires **Orca 51 or later** (`orca --version`), plus Xvfb, openbox,
  xdotool, dbus-daemon, gdbus, and python3 3.9+ on the machine where Orca runs;
  missing tools fail and list what is missing. Older Orca (such as 50 in
  Ubuntu 26.04 LTS) lacks the D-Bus recording service `asc` uses. Nothing needs
  to be installed on that machine for `asc` itself: `asc` streams its helper
  over stdin on each start.
- Orca allows only one instance per Linux user, so `asc start` refuses if you
  already have Orca running. Use watch mode to listen to your running Orca, or
  run an isolated Orca under another user account via remote mode (for example,
  `ssh = "asc@localhost"`).
- Gestures use NVDA-style names: `NVDA+T` and `Orca+T` both send Insert (Orca's
  desktop modifier).
- `asc type` sends each character directly by Unicode keysym, independent of
  the keyboard layout.
- `asc launch CMD` splits `CMD` using shell quoting rules and executes it
  directly (without a shell) on the hidden display. Launched apps run with a
  fresh, empty temporary home folder on each `asc start`.
- `asc stop` (as well as the daemon exiting or the connection dropping)
  terminates Orca, the virtual display, every launched app and its child
  processes, and deletes the temporary folder. If the helper process is
  force-killed, its direct processes still exit, though detached background
  processes may survive; the next `asc start` cleans up any leftover
  `/tmp/asc-orca-*` folders.
- Uses no network port or NVDA (see [Mode settings](#mode-settings)).

### Remote mode

Runs the screen reader on another machine (such as a Hyper-V VM) reached over
`ssh`. Remote mode's `os` setting selects which screen reader to use: `windows`
for NVDA, `linux` for Orca. Because keys go to the remote machine, there is no
panic key.

```shell
asc config set ssh me@vm --mode remote --instance vm1
asc config set os windows --mode remote --instance vm1
asc start --mode remote --instance vm1
```

- Remote mode only runs in a named [instance](#instances) (one per machine), so
  forgetting `--instance` can never accidentally drive another agent's VM. You
  can still set `mode = "remote"` in the default instance as a reminder of your
  preferred mode, but running `asc start` without `--instance` exits 2.
- `asc start` exits 2 until both `ssh` and `os` are configured for remote mode.
- `asc` runs your local `ssh` with `BatchMode=yes`, using your SSH keys, agent,
  config, and known hosts without ever prompting for a password. If SSH
  requires interactive input (such as a password or confirming a new host key),
  `asc start` fails and hints to run `ssh me@vm` manually once to confirm host
  trust and key-based login.

#### Windows (NVDA)

With `os = "windows"`, `asc` does not start NVDA: start NVDA on the remote
machine yourself and keep it running. `asc start` opens a reverse SSH tunnel
(`ssh -R`) forwarding port 26837 (remote mode's `port`) on the remote machine's
`127.0.0.1` back to `asc`'s local relay, and returns once the tunnel is up.
Connect the remote NVDA using the details from `asc status` (host
`127.0.0.1:26837`; allow this computer to be controlled).

- The remote machine needs an OpenSSH server with port forwarding enabled (on
  Windows, the OpenSSH Server optional feature). The forwarded port binds only
  to `127.0.0.1`, so no firewall rule is needed on either machine.
- If the tunnel drops after coming up, `asc` reconnects automatically (starting
  after 1 s and backing off up to 30 s); `asc status` reports
  `tunnel down (WHY)` in the meantime. If the tunnel never came up or failed
  with an SSH setup error, `asc` does not retry.
- If the configured `port` is already in use on the remote machine (for example,
  by another instance's tunnel), `asc start` fails; pick a different `port`
  for this instance.
- A fresh NVDA has Remote Access disabled by default: enable "Enable Remote
  Access" in NVDA Settings > Remote Access first, otherwise the Tools > Remote
  Access menu does not appear.
- For unattended remote NVDA setup, run `asc status --nvda-ini` to print the
  same base `nvda.ini` that desktop mode writes (Remote Access enabled,
  auto-connecting to `asc` with the certificate pre-trusted, silent synth, and
  no welcome or update prompts). Save it as UTF-8 `nvda.ini` in the remote
  NVDA's config folder (typically `%APPDATA%\nvda` for an installed NVDA) while
  NVDA is closed. Avoid `>` in Windows PowerShell 5.1, which writes UTF-16.
  Note that this replaces that NVDA's existing settings.
- `asc launch` is refused: start the app on the remote machine
  yourself, then use `asc wait`.

#### Linux (Orca)

With `os = "linux"`, `asc` starts Orca over SSH on the remote machine just like
[desktop mode](#desktop-mode) on Linux, including `asc launch`. No network port
is used (see [Mode settings](#mode-settings)); if the SSH connection drops,
commands exit 3.

### Shared mode (Windows)

Windows only (exits 2 on Linux). `asc` drives your own running NVDA, sending
keys into your real Windows session. NVDA connects the same way as in watch mode
using the same key, certificate, and (by default) port. Because they share
credentials, an NVDA that previously connected in watch mode will reconnect to
shared mode without prompting again; `--i-understand` is your explicit consent
to let `asc` send keys.

- `asc start` refuses unless you pass `--i-understand` every time (it is never
  saved in `config.toml`).
- Registers a [panic key](#panic-key).

## Panic key

Active only in Windows desktop mode and [shared mode](#shared-mode-windows),
where keys go to your real Windows session; modes that cannot send keys to your
local desktop (`watch`, `remote`, and Linux `desktop`) have no panic key. While
running, the global panic hotkey **Control+Shift+Alt+F12** releases any held
keys and stops `asc` immediately (and stops `asc`'s NVDA in desktop mode).

- Change it with `panic_key` in `[defaults]` (for example,
  `asc config set panic_key Control+Alt+F11`). Changes take effect on the next
  `asc start`.
- If another application already owns the hotkey, `asc start` refuses: `asc`
  never sends keys to your desktop without an active panic key.
- `asc status` prints the active panic key.

## Instances

`--instance NAME` (before or after the command), or the `ASC_INSTANCE`
environment variable, selects a named instance: an isolated `asc` with its own
`config.toml`, mode folders, and daemon running alongside the default instance
and any other named instances. Use one per agent, such as when each drives a
separate VM in remote mode (which only runs in a named instance).

- One daemon runs per instance; `asc start` refuses while one runs in any mode.
- `NAME` must be 1 to 32 lowercase letters, digits, or `-`, starting with a
  letter or digit (exit 2 otherwise).
- All instances share the portable NVDA created by `asc get-nvda`.
- Watch and shared mode connect to your own screen reader, which only the
  default instance may join: named instances refuse them (exit 2), and
  `asc setup` in a named instance does not offer them (remote is its default).
- Running instances cannot share a relay port (`asc start` fails if the port
  is in use). Desktop mode automatically picks a free port. In remote mode,
  `port` binds on the remote machine, so it only needs to differ among
  instances targeting the same remote host.
- `asc stop` stops only its own instance; `asc stop --all` stops every
  instance.

## Configuration

Settings live in `config.toml` (see [Files](#files)). Make it with `asc setup`
(questions, for people) and change one setting at a time with `asc config`
(for scripts and agents). Both rewrite the whole file, so comments added by
hand are not kept.

```toml
# asc settings. Change them with asc config set, or start over with asc setup.
mode = "desktop"

[defaults]
settle_ms = 800
timeout_ms = 8000

[desktop]
nvda_dir = 'C:\nvda'
nvda_settings = ["keyboard.speakTypedCharacters=False"]
```

The file stores only explicitly configured values; omitted keys use the
built-in defaults. Mode-specific settings live in `[watch]`, `[desktop]`,
`[shared]`, or `[remote]`. Paths are written as TOML literal strings (`'C:\nvda'`).
Unknown keys or invalid values cause every `asc` command to exit 2, even if the
broken setting belongs to a mode you are not currently using.

### Global settings

`mode` sits at the top level of `config.toml`; the other global settings live in
`[defaults]`.

| Setting | Default | Meaning |
|---|---|---|
| `mode` | watch, desktop on Linux | the mode `asc start` uses without `--mode` |
| `panic_key` | Control+Shift+Alt+F12 | Windows desktop/shared only: the [panic key](#panic-key), written like an `asc press` gesture: at least one of Control, Alt, or Windows (optionally with Shift, never NVDA), followed by one non-modifier key |
| `connect` | true | Windows watch/shared only: `asc start` asks your running NVDA to connect |
| `auto_update` | true | an installed `asc` [updates itself](#updates); false in any instance's config turns that off |

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

In the mode's table, like `[desktop]`. [Modes](#modes) explains each one in
context.

| Setting | Default | Meaning |
|---|---|---|
| `port` | watch and shared 6837, remote 26837, desktop a free one | NVDA modes only: relay port; in remote mode, the one NVDA connects to on its machine (Orca has none) |
| `os` | none | remote only: what the other machine runs, `windows` (NVDA) or `linux` (Orca) |
| `ssh` | none | remote only: that machine, as `ssh` takes it (`me@vm`, `ssh://me@vm:2222`, or a `Host` from your ssh config) |
| `nvda_dir` | `asc get-nvda`'s | desktop (Windows): your portable NVDA, a full path |
| `nvda_settings` | none | Windows NVDA-launched modes: `section.key=value` settings, as `--nvda-setting` takes |
| `restart_orca` | ask | watch (Linux): whether `asc start` may restart your running Orca when it lacks the recording secret (`ask`, `always`, or `never`) |

Settings used in the wrong mode or platform are refused with exit 2:

- `os` and `ssh`: remote mode only.
- `port`: NVDA modes only (refused for Orca on Linux or remote `os = "linux"`).
- `nvda_dir` and `nvda_settings`: modes where `asc` launches NVDA only
  (refused on Linux and in watch, shared, and remote modes). `nvda_settings`
  also refuses keys that `asc` manages itself (`[remote]`, speech synth,
  sound volume, keyboard layout, and browse/focus mode sounds).
- `restart_orca`: watch mode on Linux only.

## Command reference

### `asc start`

```
asc start [--mode {watch,desktop,shared,remote}]
          [--nvda-setting SECTION.KEY=VALUE]
          [--i-understand]
```

Starts the daemon in the mode and prints its status. What it starts and
checks depends on the mode: see [Modes](#modes).

| Option | Default | Meaning |
|---|---|---|
| `--mode MODE` | the `mode` setting | the mode to start, with its settings |
| `--nvda-setting SECTION.KEY=VALUE` | the mode's `nvda_settings` | an `nvda.ini` setting for the NVDA `asc` launches (repeatable) |
| `--i-understand` | off | shared mode: yes, send keys into my own session |

- `--nvda-setting` merges with the mode's `nvda_settings`, overriding any
  matching key. Names use letters, digits, and `_`, with dots for nested
  sections. Passing `--nvda-setting` in a mode where `asc` does not launch
  NVDA, or setting keys that `asc` manages itself (see
  [Mode settings](#mode-settings)), exits 2.
- In watch and shared mode on Windows, it asks your running NVDA to connect
  and waits for your approval in NVDA (see [Watch mode](#watch-mode)). If NVDA
  still isn't connected, it says so and exits 0: `asc status` shows when it is.
- Refuses if `asc` is already running, in this mode or another (the message
  names that mode), and exits 3 with the last daemon log lines if it fails to start.

```shell
asc start
asc start --mode desktop
asc start --mode remote --instance vm1
asc start --nvda-setting keyboard.speakTypedCharacters=False
```

### `asc stop`

```
asc stop [--all]
```

Releases any held keys, stops this instance's daemon and forgets the speech,
and prints `stopped: MODE` (`stopped: instances/NAME/MODE` in a named
instance). Exits 3 if nothing runs.

- `--all` stops every instance's daemon, cancels any background
  [update](#updates) check, and does not start a new one (run it before
  uninstalling `asc`).
- An unresponsive daemon (for example, from an older `asc` version or a hung
  process) still counts as running, so `asc start` and `asc setup` refuse to
  proceed. `asc stop` terminates its process (along with its managed NVDA),
  though held keys cannot be released if the daemon is unresponsive.
  If a daemon accepts a stop request but has not exited after 30 s, `asc stop`
  terminates it the same way.
- If the OS refuses to terminate the process, `asc stop` fails and leaves the
  state file in place.

### `asc status`

```
asc status [--nvda-ini | --all]
```

Shows the mode, whether `asc` is recording and the screen reader is connected, the
browse/focus mode and the speech log:

```
mode: watch (keys refused)
recording: yes
nvda: connected
browse/focus: unknown
speech: 12 lines kept, latest 12
```

- When NVDA isn't connected it prints how to connect it.
- `browse/focus` is `unknown` until the screen reader first switches modes.
- A `last error:` line says why the last failed connection was dropped (for
  example a wrong channel key), until NVDA next joins.
- When the mode has a [panic key](#panic-key), it shows it.
- If `asc` isn't running, it exits 3.
- With Orca the line is `orca: connected`.
- In remote mode `over ssh: DEST` names the machine, followed for NVDA by
  `tunnel up` or `tunnel down (WHY)`.
- `--all` lists every configured or running [instance](#instances) (the default
  instance and each named one) with its mode, remote machine if any, and
  whether it is running and connected. It exits 0 even when nothing runs, and
  cannot be combined with `--nvda-ini` (exit 2).
- `--nvda-ini` prints a ready-to-use `nvda.ini` for connecting a remote Windows
  NVDA unattended (see [Remote mode](#remote-mode)) instead of the normal
  status output. Refused in other modes or with Orca.

### `asc speech`

```
asc speech --since N
```

Prints what the screen reader said after line `N`, one numbered line per
utterance, and mode changes:

```
11 said: "Recycle Bin list item focused 1 of 2"
12 mode: focus
```

| Option | Default | Meaning |
|---|---|---|
| `--since N` | (required) | lines after line `N` (`0` for everything still kept) |

Prints `(nothing said after N)` when there is no new speech. The in-memory log
keeps up to 10,000 lines, and a single reply returns up to about 32 MB of
speech; if lines after `N` were evicted or truncated, the output begins with
`(K earlier lines dropped)`. Passing an `N` greater than the latest line number
exits 2, since line numbers reset on each `asc start`.

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

- On timeout it exits 1 and prints what the screen reader did say, numbered like
  `speech`, so its last number is still the next `--since`.
- An `N` after the latest line exits 2, as in `speech`.
- If the screen reader disconnects during `wait`, `wait` keeps waiting because
  NVDA reconnects on its own. If it is still disconnected when the timeout
  expires, `wait` exits 3 instead of 1 (including the last connection error, if
  any). Unauthenticated connections that close before joining the channel do not
  interrupt `wait`.

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

`press`, `type` and `launch` take no `--since`: they print only what the screen
reader said while they ran, numbered the same way. Their last number, or `N` in
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
| `--expect REGEX` | none | wait up to `--timeout` for a match; on timeout, exit 1 and print what was said, numbered |
| `--settle MS` | `settle_ms`, 400 | quiet period, 50 to 10000 |
| `--timeout MS` | `timeout_ms`, 5000 | deadline for the whole command, including waiting for the keyboard |

- Gestures are written as NVDA writes them: `Tab`, `H`, `NVDA+Tab`,
  `Control+Home`, `NVDA+Shift+Up` (`Orca+` is an alias for `NVDA+`). Case
  doesn't matter. Aliases such as `Ctrl`, `Esc`, `Del`, `PgDn`,
  `Win` and `Super` work. Numpad keys are sent as NVDA's desktop layout expects
  them, with Num Lock off. Look commands up with [`asc keys`](#asc-keys).
- Every gesture is checked before the first is sent. An unknown key exits 2
  with suggestions.
- Concurrent `press` or `type` commands never interleave. A second command
  waits up to its own `--timeout` for the keyboard lock, failing with exit 3 if
  it times out.

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
keyboard layout (with Orca, each character is sent as itself), then
waits for one quiet period at the end. Prints the new
speech like `press`. If any character can't be typed (for example emoji on
Windows, or control characters with Orca), it exits 2, lists them and sends
nothing.

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
asc keys [--mode {watch,desktop,shared,remote}] [--layout {desktop,laptop}] [--lang LANG]
         [--braille | --no-braille] [SEARCH ...]
```

Prints commands from the screen reader's own reference whose name, keys,
description or section contain every `SEARCH` word, one per line, or
`(no match)`. It needs no running `asc`.

```
NVDA+t  Report title: Reports the title of the currently active window. ...
h  heading
```

For Orca, keys are in `asc`'s names, ready for `asc press`:

```
Orca+NumpadEnter  Present the title bar
H and Shift+H  Next and previous heading
```

| Argument or option | Default | Meaning |
|---|---|---|
| `SEARCH` | none (all) | words that must all appear |
| `--mode MODE` | the `mode` setting | whose NVDA or Orca to read |
| `--layout {desktop,laptop}` | `keys_layout`, desktop | the keyboard layout's keys |
| `--lang LANG` | en | reference language, like `de` or `pt_BR`, falling back to `en` |
| `--braille`, `--no-braille` | off | include NVDA's rows for braille displays |

- **Windows (NVDA):** reads `documentation\<lang>\keyCommands.html` from
  desktop mode's portable NVDA, else from your own NVDA (the running
  `nvda.exe`, then the uninstall registry entry). Single letter navigation keys
  (`h  heading`) work in browse mode; add Shift to go back.
- **Linux (Orca):** reads Orca's help, `help/<lang>/orca/commands_*.page` in
  each of `$XDG_DATA_DIRS` (`/usr/local/share:/usr/share` when unset), falling
  back to English.
- **Remote mode:** reads the reference on the other machine over ssh, using
  the same Windows or Linux lookup according to `os`.
- If no reference is found it fails and says where it looked.

```shell
asc keys heading
asc keys --layout laptop current line
```

### `asc launch`

```
asc launch [--title REGEX] [--timeout MS] CMD
```

Starts `CMD` and waits until the screen reader says something matching `--title`,
or anything at all if `--title` is left out, then for quiet. Prints what the
screen reader said, numbered like `press`. Refused in remote mode with NVDA: start
the app on that machine and use `asc wait`.

| Argument or option | Default | Meaning |
|---|---|---|
| `CMD` | (required) | the command to start |
| `--title REGEX` | anything | wait until the screen reader says this |
| `--timeout MS` | `launch_timeout_ms`, 30000 | how long to wait, at most 600000 |

- Local commands run in your current working directory. A command that can't
  start exits 3, and the screen reader disconnecting meanwhile is handled as in
  `wait`.
- **Windows (NVDA):** `launch` brings the new window forward so NVDA hears it,
  except in watch mode, where it may stay behind. On timeout it exits 1 with
  what was said, numbered, ending with `New windows:` listing any windows that
  opened during the wait (or `(none)`), and noting the exit code if the
  launched process exited non-zero.
- **Linux (Orca):** `CMD` runs on Orca's display (see
  [Desktop mode](#desktop-mode)), or in watch mode on your desktop. On timeout
  it exits 1 with what was said, numbered.

```shell
asc launch "notepad.exe" --title "Notepad"
asc launch "py -m myapp" --title "My App" --timeout 60000
```

### `asc get-nvda`

```
asc get-nvda [--version NVDA_VERSION] [--installer FILE]
```

Windows only (exits 2 on Linux). Downloads the NVDA installer from NV Access and
extracts a portable copy to `%LOCALAPPDATA%\agent-speech-check\nvda\portable`
for desktop mode. After a successful download, cached installers for older
versions are removed. The portable folder is replaced only after extraction
finishes, so a failed run keeps any previous copy intact.

| Option | Default | Meaning |
|---|---|---|
| `--version NVDA_VERSION` | the latest | the NVDA version, shaped like `2026.2` (else exit 2) |
| `--installer FILE` | none | use this local installer, no download |

- It refuses while any `nvda.exe` runs, because the installer
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

1. The mode: on Windows, watch, desktop, shared, or remote; on Linux, watch,
   desktop, or remote.
2. Desktop mode: which NVDA (Windows only; not asked for Orca).
3. Watch mode on Linux: whether `asc` may restart your running Orca when it
   lacks the recording secret (`ask`, `always`, or `never`).
4. Remote mode: what the other machine runs, and its ssh destination.
5. Watch, shared, and remote mode with `os = "windows"`: your NVDA's keyboard
   layout (not asked for Orca).

When it is missing on Windows, it offers to download NVDA for desktop mode.
Timings are not asked; change them with `asc config set` when needed.

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
- It refuses while `asc` runs.
- In a named [instance](#instances) it offers only desktop and remote
  mode (remote by default).
- In the default instance, remote saves only `mode = "remote"` and says to run
  `asc setup --instance VMNAME`: remote mode runs only in a named instance.

### `asc config`

```
asc config [list] [--mode MODE]
asc config get NAME [--mode MODE]
asc config set NAME VALUE [VALUE ...] [--mode MODE]
asc config unset NAME [--mode MODE]
asc config path
```

Shows or changes the settings in `config.toml` (see
[Configuration](#configuration)). Omitting `ACTION` defaults to `list`.

| Action | Does |
|---|---|
| `list` | prints every setting as `name = value (source)`; the source is the mode's name, `global` or `default` (default when `ACTION` is omitted) |
| `get` | prints the value only |
| `set` | changes it and prints `name = value (where)` |
| `unset` | removes the setting and reverts to the default |
| `path` | prints where `config.toml` is (works even when the file is broken) |

- Mode-specific settings are written to the table for `--mode`, or to the
  configured default `mode` if `--mode` is omitted. Global settings and
  command defaults do not accept `--mode` (exit 2); all of them except `mode`
  are stored in `[defaults]`.
- Unsetting a mode's last setting removes its table.
- List settings (`nvda_settings`) accept multiple values and replace
  the entire list. Paths are normalized to full paths. Booleans are `true` or
  `false`.
- The entire config is validated before writing, so invalid combinations (such
  as setting `nvda_dir` in watch mode) exit 2 without changing the file. Unknown
  setting names exit 2 and suggest close matches.
- Changes to a running mode's settings (or `panic_key`) take effect on the next
  `asc start` (and `asc config` prints a notice). Command defaults take effect
  immediately.

```shell
asc config list
asc config set mode desktop
asc config set nvda_dir C:\nvda --mode desktop
asc config unset settle_ms
```

### `asc uninstall`

```
asc uninstall [--keep-data] [--yes]
```

Linux only (exits 2 on Windows with a hint to use Settings > Apps). Removes an
`asc` installation unpacked from a Linux release tarball (the files under
`~/.local/share/agent-speech-check` and any `asc` symlink pointing into it).
First cancels any background [update](#updates) check and stops every running
instance's daemon.

| Option | Default | Meaning |
|---|---|---|
| `--keep-data` | off | keep settings and data in `~/.local/state/agent-speech-check` |
| `--yes` | off | uninstall without asking (deletes settings and data unless `--keep-data` is also passed) |

- Without `--yes`, it asks whether to keep your settings and data (unless
  `--keep-data` is given) and then asks to confirm uninstalling. Without a
  terminal, it exits 2 without changing anything.
- Non-release installs on Linux (such as `uv tool` or running from a git clone)
  refuse without changing anything.
- If you used watch mode, restart Orca or log out so Orca drops `asc`'s
  recording secret.

```shell
asc uninstall
asc uninstall --keep-data
asc uninstall --yes
```

## Files

### Windows

| Path | Holds |
|---|---|
| `%LOCALAPPDATA%\agent-speech-check\config.toml` | the settings (see [Configuration](#configuration)) |
| `%LOCALAPPDATA%\agent-speech-check\<mode>\` | one mode's state file, `daemon.log`, and the relay certificate and channel key (shared mode uses watch mode's) |
| `%LOCALAPPDATA%\agent-speech-check\nvda\portable` | the portable NVDA that `asc get-nvda` makes |
| `%LOCALAPPDATA%\agent-speech-check\instances\<name>\` | a [named instance's](#instances) `config.toml` and mode folders |
| `%LOCALAPPDATA%\agent-speech-check\update\` | the last [update](#updates) check's `update.log` and `msiexec.log`, and the MSI it downloaded |

The certificate and key are created once and kept, because NVDA pins the
certificate.

The MSI installer stops every `asc` daemon before upgrading, repairing, or
uninstalling `asc`. Upgrades always keep `%LOCALAPPDATA%\agent-speech-check`.
Uninstalling deletes that folder unless you opt to keep it: uninstalling from
Control Panel or Settings shows a "Keep my settings and data" checkbox, and a
silent uninstall preserves it only when passed `KEEPDATA=1`
(`msiexec /x FILE.msi /qn KEEPDATA=1`).

### Linux

| Path | Holds |
|---|---|
| `~/.local/state/agent-speech-check/` (or `$XDG_STATE_HOME/agent-speech-check/`) | `config.toml`, the desktop folder, watch mode's `orca-secret`, `instances/`, and `update/` with `update.log` and the tarball |
| `~/.local/share/agent-speech-check/` | the installed `asc`: `versions/VERSION/` and the `current` link to the one that runs |

To remove a tarball install on Linux, run [`asc uninstall`](#asc-uninstall).

## Updates

An `asc` installed by the MSI, or unpacked from the Linux tarball as the
[README](../README.md#install) describes, keeps itself up to date:

- After `asc stop`, when no `asc` daemon is running in any instance, it checks
  in the background (at most once per day) for a newer
  [release](https://github.com/nibarAhmed/agent-speech-check/releases/latest).
- It downloads the release's installer from GitHub over HTTPS, verifies its
  SHA-256 against the digest GitHub lists, and installs it while preserving
  your settings and data. It never downgrades to an older version.
- Updates only install while no `asc` process is running. If one is active,
  `asc` defers the update and tries again on the next `asc stop` after at least
  one hour.
- `asc config set auto_update false` turns auto-updates off. Setting
  `auto_update = false` in any instance's `config.toml` disables updates for all
  instances, since they share the same `asc` installation.
- Auto-updates are skipped while the `CI` environment variable is set (`false`,
  `0`, or empty count as unset).
- Non-release installs (such as running from a git clone) never update
  themselves.

### Windows

Downloads the MSI and runs it quietly as an upgrade. While the upgrade is in
progress, other `asc` commands fail with a hint to try again in a minute.

### Linux

Downloads the tarball, unpacks the new version beside the current one, and swaps
the `current` symlink atomically, so a crash always leaves a working version. It
then deletes all older versions except the one that ran the update.
