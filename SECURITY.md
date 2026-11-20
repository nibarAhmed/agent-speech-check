# Security

`asc` handles sensitive data: screen reader speech (NVDA or Orca) can include passwords, private
messages and email, and `asc` can press keys on a computer.

## Reporting a problem

Please report security issues privately, not in a public issue: use this repository's private
vulnerability reporting (the Security tab, Report a vulnerability). Include what you found, how to
reproduce it and what an attacker could do with it.

## What `asc` promises

- Speech stays in memory: `asc` never writes it to a file. Logs never contain speech text or tokens.
- The daemon's control port listens on 127.0.0.1 only and needs a random token.
- Watch mode never sends keys; the daemon enforces this.
  Shared mode uses watch mode's key and certificate, so an NVDA you connected for watch mode also
  joins shared mode without asking again. Shared mode starts only with `--i-understand`.
- `asc` never writes your own NVDA configuration.
- An installed `asc` (the MSI, or the Linux tarball) updates itself only from this repository's
  GitHub releases, over HTTPS, and only with an MSI or tarball whose SHA-256 matches the one GitHub
  lists. `auto_update = false` turns this off.
