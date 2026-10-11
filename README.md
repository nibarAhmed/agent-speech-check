# Agent Speech Check

Let AI agents test your app with a real NVDA.

AI coding agents can write accessible software but can't hear it. `asc` lets an
agent, a person or CI use the NVDA screen reader the way a blind user does: press
keys, get NVDA's speech back as text, and check it. It works with any Windows app.

`asc` connects to NVDA as a Remote Access client (NVDA 2025.1 or newer), so
nothing is installed inside NVDA.

**Status: early development (M0).** Nothing is usable yet. See
`docs/planning/agent-speech-check-plan.md` for the plan and `docs/cli.md` for the
commands as they land.

## Modes (planned)

- **watch**: listen to your own NVDA while you drive. Never sends keys.
- **desktop**: `asc` runs its own silent portable NVDA on the desktop.
- **sandbox**: `asc` runs its own NVDA inside Windows Sandbox, so your NVDA is never disturbed.
- **shared**: drive your own NVDA, only with an explicit opt-in and a panic hotkey.

## Development

```
uv sync
uv run poe check
```

See `CONTRIBUTING.md`.

## License

MIT. NVDA is a trademark of NV Access; this project is not affiliated with NV Access.
