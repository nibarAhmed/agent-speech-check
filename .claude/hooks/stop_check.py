"""Stop hook: the fast gate (ruff, mypy, vulture, unit tests).

If anything fails it blocks stopping (exit 2) and feeds the failures back. When
Claude Code reports `stop_hook_active` (it is already continuing because of this
hook), a second failure is reported but does not block again, so a problem
Claude cannot fix never loops forever. e2e tests stay out of this hook.
"""

from __future__ import annotations

import json
import subprocess
import sys

STEPS: list[list[str]] = [
    ["ruff", "format", "--check", "."],
    ["ruff", "check", "."],
    ["mypy"],
    ["vulture"],
    ["pytest", "tests/unit", "-q", "--no-cov", "-p", "no:cacheprovider"],
]
MAX_OUTPUT = 4000


def main() -> int:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    already_continuing = bool(payload.get("stop_hook_active"))

    failures: list[str] = []
    for step in STEPS:
        result = subprocess.run(step, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            failures.append(f"$ {' '.join(step)}\n{output[-MAX_OUTPUT:]}")

    if not failures:
        return 0
    print("Fast gate failed. Fix these before finishing:\n", file=sys.stderr)
    print("\n\n".join(failures), file=sys.stderr)
    return 0 if already_continuing else 2


if __name__ == "__main__":
    sys.exit(main())
