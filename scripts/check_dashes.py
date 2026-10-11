"""Fail if any file contains an em dash (U+2014).

With no arguments, checks every file git tracks or would add. With arguments
(as pre-commit passes them), checks just those files. Binary files are skipped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EM_DASH = chr(0x2014)


def candidate_files(args: list[str]) -> list[Path]:
    if args:
        return [Path(a) for a in args]
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(line) for line in listed.splitlines() if line]


def find_dashes(path: Path) -> list[int]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []
    return [n for n, line in enumerate(text.splitlines(), start=1) if EM_DASH in line]


def main(args: list[str]) -> int:
    failures = [(path, n) for path in candidate_files(args) for n in find_dashes(path)]
    for path, n in failures:
        print(f"{path}:{n}: em dash (U+2014); use a colon, comma, parentheses or a new sentence")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
