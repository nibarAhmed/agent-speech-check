# `asc` command reference

Status: M0. Only `asc --help` and `asc --version` exist. The commands below are
the plan for M1 to M4 (see `docs/planning/agent-speech-check-plan.md`), and this
file is updated with every command change.

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

## Planned commands

- Session: `asc start`, `asc stop`, `asc status`
- Keys: `asc press`, `asc type`, `asc keys`
- Speech: `asc wait`, `asc speech`, `asc mark`, `asc clear`
- Apps: `asc launch`

Every command takes `--target NAME`.
