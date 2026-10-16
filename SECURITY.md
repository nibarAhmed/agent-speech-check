# Security

`asc` handles sensitive data: NVDA's speech can include passwords, private messages and email,
and `asc` can press keys on a computer.

## Reporting a problem

Please report security issues privately, not in a public issue: use this repository's private
vulnerability reporting (the Security tab, Report a vulnerability). Include what you found, how to
reproduce it and what an attacker could do with it.

## What `asc` promises

- Speech stays in memory: `asc` never writes it to a file. Logs never contain speech text or tokens.
- The daemon's control port listens on 127.0.0.1 only and needs a random token.
- Watch mode never sends keys; the daemon enforces this.
- `asc` never writes your own NVDA configuration.
