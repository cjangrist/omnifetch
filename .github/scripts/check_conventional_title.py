"""Validate a commit/PR title against gitmoji + Conventional Commits.

Shared by the pre-commit ``commit-msg`` hook (reads the commit message file in
``sys.argv[1]``) and the PR-title CI check (reads the ``PR_TITLE`` env var). One
pattern governs both gates; an optional leading gitmoji is permitted.
"""

from __future__ import annotations

import os
import re
import sys

_TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"
_PATTERN = re.compile(
    r"^(?:[^\x00-\x7f]+\s*|:[a-z0-9_+-]+:\s*)?"
    rf"(?:{_TYPES})(?:\([^)]+\))?!?: .+"
)


def _header() -> str:
    """Return the first non-empty line of the title or commit message."""
    title = os.environ.get("PR_TITLE")
    if title is None:
        with open(sys.argv[1], encoding="utf-8") as handle:
            title = handle.read()
    lines = [line for line in title.splitlines() if line.strip()]
    return lines[0].strip() if lines else ""


def main() -> int:
    """Exit 0 if the header matches the convention, else 1 with guidance."""
    header = _header()
    if _PATTERN.match(header):
        print(f"OK: {header}")
        return 0
    print(f"Invalid title: {header!r}", file=sys.stderr)
    print(
        "Expected '<gitmoji> <type>(<scope>): <summary>', "
        "e.g. '✨ feat: add the thing'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
