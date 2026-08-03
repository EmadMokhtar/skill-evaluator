#!/usr/bin/env python3
"""Fail a PR that changes skill-eval source without touching documentation.

This is deliberately a heuristic, not a proof. It cannot tell a stale sentence
from a fresh one; it only notices that the package changed and no documented
surface did. Pure refactors, dependency bumps and internal-only changes will
trip it -- that is the accepted cost. The escape hatch is the `no-docs-needed`
label on the PR, checked in the workflow rather than here.

The precise checks (a flag, config key, or assertion kind that exists in code
but nowhere in the docs) live in tests/test_docs.py.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Sequence

SOURCE_PREFIX = "src/skill_eval/"

# What counts as having documented the change. docs/superpowers/ is deliberately
# absent: it is a historical archive of specs and plans, and adding one is not
# the same as documenting a code change for users.
DOCS_PATHS = ("docs/", "README.md", "ARCHITECTURE.md", "mkdocs.yml")
EXCLUDED_DOCS_PREFIX = "docs/superpowers/"

LABEL = "no-docs-needed"


def changed_files(base: str, head: str) -> list[str]:
    """Paths changed between `base` and `head`, as git reports them."""
    completed = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def source_changes(paths: Iterable[str]) -> list[str]:
    """The changed paths that live inside the package."""
    return [path for path in paths if path.startswith(SOURCE_PREFIX)]


def touches_docs(paths: Iterable[str]) -> bool:
    """True when any changed path is a documented surface."""
    return any(
        path.startswith(DOCS_PATHS) and not path.startswith(EXCLUDED_DOCS_PREFIX) for path in paths
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: check_docs_updated.py <base-ref> <head-ref>")
        return 2

    paths = changed_files(args[0], args[1])
    sources = source_changes(paths)
    if not sources or touches_docs(paths):
        return 0

    listed = "\n".join(f"  - {path}" for path in sources)
    print(
        "This PR changes skill-eval source but no documentation:\n"
        f"{listed}\n\n"
        "Update whichever of these the change affects:\n"
        "  - docs/            user-facing documentation\n"
        "  - ARCHITECTURE.md  design, invariants, extension points\n"
        "  - README.md        the landing page\n"
        "  - mkdocs.yml       navigation\n\n"
        f"If the change genuinely needs no documentation, add the `{LABEL}` "
        "label to the PR."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
