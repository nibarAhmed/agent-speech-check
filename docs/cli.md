# `asc` command reference

Status: M1 (watch mode). Commands not listed under "Commands" are planned for
M2 to M4 (see `docs/planning/agent-speech-check-plan.md`). This file is updated
with every command change.

Results go to stdout and errors to stderr. An error prints `error: <message>`
and, when there is one, `hint: <what to do>`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | A `wait` or `--expect` failed |
| 2 | Usage error |
| 3 | Not connected, or no target |
| 4 | Refused |
| 5 | Protocol error |
| 6 | Internal error (a bug in `asc`; the message says where the log is) |
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
port = 6837         # relay port; watch defaults to 6837, other modes pick a free one
keys = false        # may send keys; never true in watch mode
launch = true       # desktop: false if NVDA is started elsewhere and connects itself
```

Unknown keys are errors. A target that isn't listed gets the defaults for
`--mode` (watch if left out).

Each target has a folder `%LOCALAPPDATA%\agent-speech-check\<target>\` with the
state file, `daemon.log`, and the relay certificate and channel key. The
certificate and key are created once and kept, because NVDA pins the
certificate.

## Commands

### `asc start [--mode watch|desktop|sandbox|shared]`

Starts the daemon for the target and prints its status. Exits 4 if it is
already running, and 3 with the last daemon log lines if it fails to start.

**Watch mode** is your own NVDA. `asc` listens and never sends keys. Connect
once in NVDA: Tools, Remote Access, Connect; choose to allow this computer to
be controlled, enter the host and key `asc start` printed, and trust the
certificate when NVDA asks. `asc` never writes your NVDA config.

In M1, desktop mode works only with `launch = false` (NVDA is started
elsewhere and connects to the relay itself). Sandbox and shared mode exit 2.

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
`unknown` until NVDA first switches modes. If `asc` isn't running, it exits 3
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
  `--timeout` (default 5000 ms), and exits 1 if it can't get the keyboard.

## Planned commands

- Keys: `asc type`, `asc keys`
- Apps: `asc launch`
