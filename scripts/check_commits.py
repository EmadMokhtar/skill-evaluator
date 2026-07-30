#!/usr/bin/env python3
"""Verify every commit in a range uses Conventional Commits.

Delegates to `cz check` so this and the PR-title check apply identical rules,
rather than a regex here drifting from Commitizen's grammar.

Commits listed in scripts/legacy-commits.txt are exempt: they predate the
convention being adopted. That file should only ever shrink.

Usage: check_commits.py <base-ref> <head-ref>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LEGACY_FILE = Path(__file__).parent / "legacy-commits.txt"
SEP = "\x1f"  # unit separator — safe against subjects containing anything printable


def _legacy_shas() -> set[str]:
    """Full SHAs of commits exempted for predating the convention."""
    if not LEGACY_FILE.is_file():
        return set()
    shas = set()
    for line in LEGACY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            shas.add(line)
    return shas


def _commits(base: str, head: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", f"--format=%H{SEP}%s", f"{base}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        (sha, subject)
        for sha, _, subject in (line.partition(SEP) for line in out.splitlines())
        if sha
    ]


def _is_conventional(subject: str) -> bool:
    return (
        subprocess.run(
            ["cz", "check", "--message", subject],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    base, head = sys.argv[1], sys.argv[2]
    legacy = _legacy_shas()
    commits = _commits(base, head)

    checked = 0
    offenders: list[tuple[str, str]] = []
    for sha, subject in commits:
        if sha in legacy:
            print(f"skip (pre-convention) {sha[:9]} {subject}")
            continue
        checked += 1
        if not _is_conventional(subject):
            offenders.append((sha, subject))

    if offenders:
        print(f"\n{len(offenders)} commit(s) are not Conventional Commits:\n", file=sys.stderr)
        for sha, subject in offenders:
            print(f"  {sha[:9]} {subject}", file=sys.stderr)
        print(
            "\nExpected '<type>[scope][!]: <description>', "
            "type one of feat|fix|docs|refactor|test|perf|build|ci|chore|style|revert.\n"
            "Reword with: git commit --amend  (or git rebase to fix older commits).",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {checked} commit(s) in {base}..{head} are Conventional Commits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
