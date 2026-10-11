"""PostToolUse hook for Edit and Write.

Reads the hook JSON on stdin. For a `.py` file it runs `ruff format` and
`ruff check --fix`. For any file it blocks (exit 2) if the file contains an em
dash, reporting the lines, or if ruff leaves unfixable problems.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EM_DASH = chr(0x2014)


def main() -> int:
    payload = json.load(sys.stdin)
    raw_path = payload.get("tool_input", {}).get("file_path")
    if not isinstance(raw_path, str):
        return 0
    path = Path(raw_path)
    if not path.is_file():
        return 0

    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = ""
    problems.extend(
        f"{path}:{n}: em dash (U+2014); use a colon, comma, parentheses or a new sentence"
        for n, line in enumerate(text.splitlines(), start=1)
        if EM_DASH in line
    )

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
