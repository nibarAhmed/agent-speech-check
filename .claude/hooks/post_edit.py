"""PostToolUse hook for Edit and Write.

Reads the hook JSON on stdin. For a `.py` file it runs `ruff format` and
`ruff check --fix`. For any file it blocks (exit 2) if `scripts/check_dashes.py`
finds an em dash, or if ruff leaves unfixable problems.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    payload = json.load(sys.stdin)
    raw_path = payload.get("tool_input", {}).get("file_path")
    if not isinstance(raw_path, str):
        return 0
    path = Path(raw_path)
    # The hook command runs from the project root. Leave files outside the repo
    # (memory, global settings) alone: their rules are not this repo's rules.
    if not path.is_file() or not path.resolve().is_relative_to(Path.cwd().resolve()):
        return 0

    problems: list[str] = []
    dashes = subprocess.run(
        [sys.executable, "scripts/check_dashes.py", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if dashes.returncode != 0:
        problems.append(dashes.stdout.strip())

    if path.suffix == ".py":
        subprocess.run(["ruff", "format", "--quiet", str(path)], check=False)
        lint = subprocess.run(
            ["ruff", "check", "--fix", "--quiet", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if lint.returncode != 0:
            problems.append(lint.stdout.strip() or lint.stderr.strip())

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
