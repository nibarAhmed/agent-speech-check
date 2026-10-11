# Agent Speech Check

Let AI agents test your app with a real NVDA.

AI coding agents can write accessible software but can't hear it. `asc` lets an
agent, a person or CI use the NVDA screen reader the way a blind user does: press
keys, get NVDA's speech back as text, and check it. It works with any Windows app.

`asc` connects to NVDA as a Remote Access client (NVDA 2025.1 or newer), so
nothing is installed inside NVDA.

**Status: all four modes work (tested against NVDA 2026.2).** See
`docs/cli.md` for the commands and `docs/protocol.md` for how `asc` talks to
NVDA.

## Modes

- **watch**: listen to your own NVDA while you drive. Never sends keys.
- **desktop**: `asc` runs its own silent portable NVDA on the desktop.
- **sandbox**: `asc` runs its own NVDA inside Windows Sandbox, so your NVDA is never disturbed.
- **shared**: drive your own NVDA, only with an explicit opt-in and a panic hotkey.

## Claude Code skill

`skills/asc/SKILL.md` tells an agent when to use `asc`, the workflow and the
gotchas. Install it for all your projects by copying the `skills/asc` folder to
`~/.claude/skills/`, and copy it again after it changes.

## Development

```
uv sync
uv run poe check
```

See `CONTRIBUTING.md`.

## License

MIT. NVDA is a trademark of NV Access; this project is not affiliated with NV Access.
